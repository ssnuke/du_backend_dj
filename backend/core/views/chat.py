import asyncio
import logging
import mimetypes
import os
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Max, Q
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
    ChatTabsConfig,
    Ir,
    Sticker,
    StickerPack,
    StickerPackSubscription,
)
from core.utils.audio_transcode import SKIP_TRANSCODE_MIMES, transcode_voice_note

from datetime import timedelta


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


def _can_chat_with(requester, target):
    """
    Return True if requester is allowed to open a DM with target.
    Mirrors the candidate-list logic in ChatCandidates so the two stay in sync.
    """
    if requester.ir_id == target.ir_id:
        return False  # can't DM yourself

    level = requester.ir_access_level

    if level == AccessLevel.ADMIN:
        return True

    if level == AccessLevel.CTC:
        if target.ir_access_level == AccessLevel.ADMIN:
            return True
        return requester.can_view_ir(target)

    if level == AccessLevel.LDC:
        if target.ir_access_level == AccessLevel.ADMIN:
            return True
        if requester.is_in_subtree(target):
            return True
        return requester._is_upline_at_levels(target, [AccessLevel.CTC])

    if level == AccessLevel.LS:
        if target.ir_access_level == AccessLevel.ADMIN:
            return True
        return requester.can_view_ir(target)

    if level == AccessLevel.GC:
        # GC can chat with their own downline (team members) and their upline LDC(s)
        if requester.is_in_subtree(target):
            return True
        return requester._is_upline_at_levels(target, [AccessLevel.LDC])

    # IR — only with people who can already view them (handled by can_view_ir on the other side)
    return requester.can_view_ir(target)


def _is_room_member(room, ir):
    return ChatRoomMember.objects.filter(room=room, ir=ir).exists()


ROOM_LIST_CACHE_TTL = 30  # seconds — a safety net; correctness comes from explicit invalidation below
ROOM_LIST_CACHE_KEY_FMT = "chat_rooms:{ir_id}"


def invalidate_chat_rooms_cache(ir_ids):
    """
    Drop the cached room-list response for every given ir_id. Call this after
    any mutation that changes what ChatRoomListCreate.get would serialize for
    those users: new/renamed/deleted room, message sent/edited/deleted,
    members added/removed, mute/unmute (self only), pin/unpin (room or
    message), room image change. Both the HTTP views and the WS consumer
    handlers mutate the same data through separate code paths, so both must
    call this.
    """
    ir_ids = [ir_id for ir_id in ir_ids if ir_id]
    if not ir_ids:
        return
    cache.delete_many([ROOM_LIST_CACHE_KEY_FMT.format(ir_id=ir_id) for ir_id in ir_ids])


def _room_member_ir_ids(room_id):
    return list(ChatRoomMember.objects.filter(room_id=room_id).values_list("ir_id", flat=True))


def _serialize_room(room, requester=None, *, precomputed=None):
    """
    precomputed (optional dict) lets callers that already batch-fetched data for
    many rooms at once (see ChatRoomListCreate.get) avoid a fresh set of queries
    per room. Keys: last_message, unread_count, membership, other_member, member_count.
    Falls back to the old per-room queries when a key isn't supplied, so single-room
    call sites don't need to change.
    """
    precomputed = precomputed or {}
    has = precomputed.__contains__

    last_message = precomputed.get("last_message") if has("last_message") else room.messages.order_by("-id").first()

    is_muted = False
    muted_until = None
    membership = precomputed.get("membership") if has("membership") else None
    if not has("membership") and requester:
        membership = ChatRoomMember.objects.filter(room=room, ir=requester).first()
    if membership:
        # Auto-expire mutes
        if membership.is_muted and membership.muted_until and membership.muted_until <= timezone.now():
            membership.is_muted = False
            membership.muted_until = None
            membership.save(update_fields=["is_muted", "muted_until"])
        is_muted = membership.is_muted
        muted_until = membership.muted_until

    unread_count = precomputed.get("unread_count") if has("unread_count") else 0
    if not has("unread_count") and requester:
        unread_qs = room.messages.exclude(receipts__reader=requester).exclude(sender=requester)
        # A member added with history hidden can never see (or mark read)
        # anything sent before they joined — counting those messages here
        # produced an unread badge that could never reach zero no matter
        # how much the member actually read.
        if membership and membership.hide_history_before_join:
            unread_qs = unread_qs.filter(created_at__gte=membership.joined_at)
        unread_count = unread_qs.count()

    if has("other_member"):
        other_member = precomputed.get("other_member")
    else:
        other_member = None
        if room.room_type == ChatRoomType.DIRECT and requester:
            other = ChatRoomMember.objects.filter(room=room).exclude(ir=requester).select_related("ir").first()
            if other:
                other_member = {
                    "ir_id": other.ir.ir_id,
                    "ir_name": other.ir.chat_name,
                    "avatar_url": other.ir.avatar_url,
                    "last_seen": other.ir.last_seen.isoformat() if other.ir.last_seen else None,
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
        elif last_message.message_type == ChatMessageType.STICKER:
            preview = "🎨 Sticker"
        elif last_message.message_type == ChatMessageType.GIF:
            preview = "🎞️ GIF"
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
        "member_count": precomputed.get("member_count") if has("member_count") else room.memberships.count(),
        "last_message_preview": preview,
        "last_message_at": (last_message.created_at if last_message else None),
        "unread_count": unread_count,
        "other_member": other_member,
        "is_muted": is_muted,
        "muted_until": muted_until.isoformat() if muted_until else None,
    }


def _serialize_message(message, read_by_me=None):
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
    for r in message.reactions.all():
        reactions.setdefault(r.emoji, []).append({
            "ir_id": r.ir.ir_id,
            "ir_name": r.ir.chat_name,
        })

    return {
        "id": message.id,
        "room_id": message.room_id,
        "sender_ir_id": message.sender.ir_id,
        "sender_name": message.sender.chat_name,
        "sender_avatar_url": message.sender.avatar_url,
        "message_type": message.message_type,
        "content": message.content,
        "attachment_url": message.attachment_url,
        "attachment_name": message.attachment_name,
        "attachment_size": message.attachment_size,
        "attachment_duration": message.attachment_duration,
        # Use isoformat strings — channel layer (Redis) cannot serialize datetime objects
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "read_count": read_count,
        # Per-requester read state. Only meaningful for a single-recipient response
        # (e.g. the paginated message-history GET); omitted (None) on payloads
        # broadcast identically to every room member, such as message_created —
        # those recipients derive their own read state client-side instead.
        "read_by_me": read_by_me,
        "reply_to": reply_to_data,
        "is_deleted": message.is_deleted,
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "forwarded_from": message.forwarded_from,
        "reactions": reactions,
    }


class ChatRoomListCreate(APIView):
    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        cache_key = ROOM_LIST_CACHE_KEY_FMT.format(ir_id=requester_ir_id)
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        room_ids = list(ChatRoomMember.objects.filter(ir=requester).values_list("room_id", flat=True))
        rooms = list(
            ChatRoom.objects.filter(id__in=room_ids)
            .select_related("created_by", "pinned_message", "pinned_message__sender")
        )

        # Batch-fetch everything _serialize_room would otherwise re-query per room.
        last_message_ids = dict(
            ChatMessage.objects.filter(room_id__in=room_ids)
            .values("room_id")
            .annotate(last_id=Max("id"))
            .values_list("room_id", "last_id")
        )
        last_messages_by_room = {
            m.room_id: m
            for m in ChatMessage.objects.filter(id__in=[v for v in last_message_ids.values() if v])
        }

        unread_by_room = dict(
            ChatMessage.objects.filter(room_id__in=room_ids)
            .exclude(sender=requester)
            .exclude(receipts__reader=requester)
            .values("room_id")
            .annotate(c=Count("id"))
            .values_list("room_id", "c")
        )

        now = timezone.now()
        memberships = list(
            ChatRoomMember.objects.filter(room_id__in=room_ids).select_related("ir")
        )
        # Auto-expire mutes in bulk instead of per-row .save() calls.
        expired_ids = [
            m.id for m in memberships
            if m.is_muted and m.muted_until and m.muted_until <= now
        ]
        if expired_ids:
            ChatRoomMember.objects.filter(id__in=expired_ids).update(is_muted=False, muted_until=None)
            for m in memberships:
                if m.id in expired_ids:
                    m.is_muted = False
                    m.muted_until = None

        own_membership_by_room = {m.room_id: m for m in memberships if m.ir_id == requester.ir_id}

        # The naive unread_by_room count above counts every message ever sent,
        # including ones from before a member joined with history hidden —
        # those messages are permanently excluded from that member's message
        # list (see ChatRoomMessages.get's hide_history_before_join filter
        # below) so they can never be scrolled to or marked read, leaving the
        # badge stuck above zero forever. Only re-query the (usually tiny)
        # subset of rooms actually affected, rather than restructuring the
        # single aggregate query above for everyone.
        hidden_history_room_ids = [
            room_id for room_id, m in own_membership_by_room.items() if m.hide_history_before_join
        ]
        for room_id in hidden_history_room_ids:
            joined_at = own_membership_by_room[room_id].joined_at
            unread_by_room[room_id] = (
                ChatMessage.objects.filter(room_id=room_id, created_at__gte=joined_at)
                .exclude(sender=requester)
                .exclude(receipts__reader=requester)
                .count()
            )

        member_count_by_room = {}
        other_member_by_room = {}
        for m in memberships:
            member_count_by_room[m.room_id] = member_count_by_room.get(m.room_id, 0) + 1
            if m.ir_id != requester.ir_id and m.room_id not in other_member_by_room:
                other_member_by_room[m.room_id] = {
                    "ir_id": m.ir.ir_id,
                    "ir_name": m.ir.chat_name,
                    "avatar_url": m.ir.avatar_url,
                    "last_seen": m.ir.last_seen.isoformat() if m.ir.last_seen else None,
                }

        data = [
            _serialize_room(
                room,
                requester=requester,
                precomputed={
                    "last_message": last_messages_by_room.get(room.id),
                    "unread_count": unread_by_room.get(room.id, 0),
                    "membership": own_membership_by_room.get(room.id),
                    "other_member": other_member_by_room.get(room.id) if room.room_type == ChatRoomType.DIRECT else None,
                    "member_count": member_count_by_room.get(room.id, 0),
                },
            )
            for room in rooms
        ]
        response_body = {"rooms": data}
        cache.set(cache_key, response_body, ROOM_LIST_CACHE_TTL)
        return Response(response_body)

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

        if room_type == ChatRoomType.DIRECT:
            other_ir_id = next(iter(member_ids - {requester.ir_id}))
            # Direct rooms always have exactly 2 members (enforced above at creation
            # time), so matching on "has a membership for requester" AND (separately)
            # "has a membership for the other person" uniquely identifies the room
            # between these two people — no extra count check needed.
            existing_room = (
                ChatRoom.objects.filter(room_type=ChatRoomType.DIRECT, memberships__ir=requester)
                .filter(memberships__ir_id=other_ir_id)
                .first()
            )
            if existing_room:
                return Response(
                    {
                        "message": "Room already exists",
                        "room": _serialize_room(existing_room, requester=requester),
                    },
                    status=status.HTTP_200_OK,
                )

        members = list(Ir.objects.filter(ir_id__in=member_ids, status=True))
        found_ids = {member.ir_id for member in members}
        missing = sorted(list(member_ids - found_ids))
        if missing:
            return Response({"detail": f"Invalid member IDs: {', '.join(missing)}"}, status=status.HTTP_400_BAD_REQUEST)

        for member in members:
            if member.ir_id != requester.ir_id and not _can_chat_with(requester, member):
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

        invalidate_chat_rooms_cache([member.ir_id for member in members])

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

        invalidate_chat_rooms_cache(_room_member_ir_ids(room.id))

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
                        "ir_name": membership.ir.chat_name,
                        "avatar_url": membership.ir.avatar_url,
                        "ir_access_level": membership.ir.ir_access_level,
                        "joined_at": membership.joined_at,
                        "last_seen": membership.ir.last_seen.isoformat() if membership.ir.last_seen else None,
                    }
                    for membership in memberships
                ]
            }
        )


class ChatRoomMembersAdd(APIView):
    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        member_ir_ids = request.data.get("member_ir_ids") or []
        show_history = bool(request.data.get("show_history", True))

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        is_elevated = requester.ir_access_level in (AccessLevel.ADMIN, AccessLevel.CTC, AccessLevel.LDC, AccessLevel.LS)
        if room.room_type == ChatRoomType.GROUP and room.created_by_id != requester.ir_id and not is_elevated:
            return Response({"detail": "Only the group owner or an LDC/CTC/Admin can add members"}, status=status.HTTP_403_FORBIDDEN)

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
                ChatRoomMember(
                    room=room,
                    ir=member,
                    added_by=requester,
                    hide_history_before_join=not show_history,
                )
                for member in to_add
            ])
            room.save(update_fields=["updated_at"])
            invalidate_chat_rooms_cache(_room_member_ir_ids(room.id))

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
        invalidate_chat_rooms_cache(current_member_ids)

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
        after_id = request.GET.get("after_id")
        limit = request.GET.get("limit", PAGE_SIZE_DEFAULT)

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        membership = ChatRoomMember.objects.filter(room=room, ir=requester).first()
        if not membership:
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        try:
            limit = int(limit)
            if limit < 1 or limit > 100:
                limit = PAGE_SIZE_DEFAULT
        except (TypeError, ValueError):
            limit = PAGE_SIZE_DEFAULT

        # after_id is a gap-fill cursor (e.g. after a WebSocket reconnect): fetch
        # everything newer than the last message the client already has, oldest
        # first, so the client can append pages in order without missing any.
        if after_id:
            try:
                after_id_int = int(after_id)
            except (TypeError, ValueError):
                return Response({"detail": "after_id must be an integer"}, status=status.HTTP_400_BAD_REQUEST)

            qs = (
                ChatMessage.objects
                .filter(room=room, id__gt=after_id_int)
                .select_related("sender", "reply_to", "reply_to__sender")
                .prefetch_related("reactions__ir")
                .annotate(read_count=Count("receipts"))
                .order_by("id")   # oldest-of-the-gap first
            )

            if membership.hide_history_before_join:
                qs = qs.filter(created_at__gte=membership.joined_at)

            messages = list(qs[:limit])
            next_after_id = messages[-1].id if messages else after_id_int

            read_message_ids = set(
                ChatMessageReceipt.objects.filter(
                    reader=requester, message_id__in=[m.id for m in messages]
                ).values_list("message_id", flat=True)
            )

            return Response(
                {
                    "messages": [
                        _serialize_message(
                            message,
                            read_by_me=(message.sender_id == requester.ir_id or message.id in read_message_ids),
                        )
                        for message in messages
                    ],
                    "next_after_id": next_after_id,
                    "has_more": len(messages) == limit,
                }
            )

        qs = (
            ChatMessage.objects
            .filter(room=room)
            .select_related("sender", "reply_to", "reply_to__sender")
            .prefetch_related("reactions__ir")
            .annotate(read_count=Count("receipts"))
            .order_by("-id")   # newest first; ensures [:limit] gives the most-recent page
        )

        if membership.hide_history_before_join:
            qs = qs.filter(created_at__gte=membership.joined_at)

        if before_id:
            try:
                before_id_int = int(before_id)
                qs = qs.filter(id__lt=before_id_int)
            except (TypeError, ValueError):
                return Response({"detail": "before_id must be an integer"}, status=status.HTTP_400_BAD_REQUEST)

        messages = list(qs[:limit])
        next_before_id = messages[-1].id if messages else None

        read_message_ids = set(
            ChatMessageReceipt.objects.filter(
                reader=requester, message_id__in=[m.id for m in messages]
            ).values_list("message_id", flat=True)
        )

        return Response(
            {
                "messages": [
                    _serialize_message(
                        message,
                        read_by_me=(message.sender_id == requester.ir_id or message.id in read_message_ids),
                    )
                    for message in messages
                ],
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

        if message_type not in ChatMessageType.values or message_type == ChatMessageType.SYSTEM:
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

        forwarded_from = (request.data.get("forwarded_from") or "").strip() or None

        message = ChatMessage.objects.create(
            room=room,
            sender=requester,
            message_type=message_type,
            content=content,
            attachment_url=attachment_url,
            attachment_name=attachment_name,
            attachment_size=attachment_size,
            attachment_duration=attachment_duration,
            forwarded_from=forwarded_from,
        )
        room.save(update_fields=["updated_at"])
        invalidate_chat_rooms_cache(_room_member_ir_ids(room.id))

        serialized = _serialize_message(message)
        member_ir_ids_except_sender = [
            ir_id for ir_id in _room_member_ir_ids(room.id) if ir_id != requester.ir_id
        ]

        # Broadcast to all WebSocket connections in the room so every client
        # updates in real-time, not just on refresh.
        try:
            from core.chat.notify import preview_for_message
            preview_text = preview_for_message(content, message_type, attachment_name)

            async def _broadcast():
                # Mirrors ChatConsumer._handle_send_message: one group_send for
                # the room, plus a concurrency-bounded fanout (not one
                # async_to_sync call per member — that was sequential, unbounded,
                # and paid a fresh event-loop-spin per call, competing with the
                # WebSocket path for the same capped Redis connection pool. A
                # large room's message burst on this path is exactly what could
                # leave an in-flight request stuck long enough for Daphne to
                # force-kill it as "took too long to shut down".)
                channel_layer = get_channel_layer()
                semaphore = asyncio.Semaphore(settings.CHAT_FANOUT_CONCURRENCY)

                async def _send_one(member_ir_id):
                    async with semaphore:
                        await channel_layer.group_send(
                            f"user_inbox_{member_ir_id}",
                            {
                                "type": "room_preview_updated",
                                "room_id": message.room_id,
                                "last_message_preview": preview_text,
                                "last_message_at": serialized["created_at"],
                                "sender_ir_id": requester.ir_id,
                            },
                        )

                await asyncio.gather(
                    channel_layer.group_send(
                        f"chat_room_{room_id}",
                        {"type": "message_created", "message": serialized},
                    ),
                    *(_send_one(member_ir_id) for member_ir_id in member_ir_ids_except_sender),
                )

            async_to_sync(_broadcast)()
        except Exception:
            # Never block the response if the channel layer is unavailable —
            # but log it. This used to be a bare `pass`, which meant a broken
            # channel layer would silently kill every live update (both the
            # room broadcast and every member's inbox preview) with zero
            # trace in the logs — exactly the symptom of "works on refresh,
            # never live" with no visible cause.
            logging.getLogger(__name__).exception(
                "Failed to broadcast message_created/room_preview_updated for room %s", room.id
            )

        # Fire FCM push notifications the same way the WebSocket send path does
        # (core/chat/consumers.py _notify_room_members) — this REST endpoint is
        # the only send path for attachments/stickers/GIFs/forwards, and was
        # previously silent for all of them.
        #
        # notify_chat_room_members sends one HTTP request per recipient
        # token, so — unlike the WS path, which already offloads this to its
        # own executor — calling it inline here would block this (plain,
        # synchronous) view for the full multicast send. run_in_background
        # queues it on the same shared executor create_notifications uses;
        # any failure is logged inside it, not here, since this try/except
        # only covers the (fast) queuing step now.
        try:
            from core.chat.notify import notify_chat_room_members
            from core.utils.firebase_messaging import run_in_background
            run_in_background(
                notify_chat_room_members,
                room.id,
                requester.ir_id,
                requester.chat_name,
                content,
                message_type=message_type,
                attachment_name=attachment_name,
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to queue chat FCM notifications for room %s (REST path)", room.id
            )

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
        invalidate_chat_rooms_cache([requester.ir_id])

        updated_ids = [message.id for message in valid_messages]
        if updated_ids:
            try:
                channel_layer = get_channel_layer()
                # Tell live viewers of this room (e.g. the sender watching their own
                # message get read) that these messages are now read. The WebSocket
                # mark_read path already does this — this REST path is hit whenever
                # the client's socket happens to be reconnecting, so without this
                # broadcast those viewers never see the read tick update live.
                async_to_sync(channel_layer.group_send)(
                    f"chat_room_{room_id}",
                    {
                        "type": "read_receipts_updated",
                        "message_ids": updated_ids,
                        "reader_ir_id": requester.ir_id,
                        "reader_name": requester.ir_name,
                    },
                )
                # Tell the reader's own other tabs/devices that this room's unread
                # count just changed, so their room list badge updates without a
                # manual refresh.
                async_to_sync(channel_layer.group_send)(
                    f"user_inbox_{requester.ir_id}",
                    {"type": "own_read_state_updated", "room_id": room_id},
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to broadcast read_receipts_updated/own_read_state_updated for room %s", room_id
                )

        return Response(
            {
                "message": "Read receipts updated",
                "updated_count": len(valid_messages),
            }
        )


class ChatRoomMarkAllRead(APIView):
    """
    Manual escape hatch: mark every currently-unread message in this room as
    read for the requester in one shot, regardless of how many there are or
    whether the client has ever fetched/rendered them. Exists because the
    normal path (IntersectionObserver marking messages read as they scroll
    into view) depends on the client actually loading and rendering each
    unread message — for a room with a very large backlog, or if that
    client-side marking has any gap, the badge can get stuck no matter how
    much the user reads. This endpoint doesn't depend on the client having
    seen anything; it authoritatively resolves the badge from the server side.
    """

    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        membership = ChatRoomMember.objects.filter(room=room, ir=requester).first()
        if not membership:
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        unread_qs = ChatMessage.objects.filter(room=room).exclude(sender=requester).exclude(receipts__reader=requester)
        if membership.hide_history_before_join:
            unread_qs = unread_qs.filter(created_at__gte=membership.joined_at)

        unread_ids = list(unread_qs.values_list("id", flat=True))
        ChatMessageReceipt.objects.bulk_create(
            [ChatMessageReceipt(message_id=message_id, reader=requester) for message_id in unread_ids],
            ignore_conflicts=True,
        )
        invalidate_chat_rooms_cache([requester.ir_id])

        if unread_ids:
            try:
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f"chat_room_{room_id}",
                    {
                        "type": "read_receipts_updated",
                        "message_ids": unread_ids,
                        "reader_ir_id": requester.ir_id,
                        "reader_name": requester.ir_name,
                    },
                )
                async_to_sync(channel_layer.group_send)(
                    f"user_inbox_{requester.ir_id}",
                    {"type": "own_read_state_updated", "room_id": room_id},
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "Failed to broadcast read_receipts_updated/own_read_state_updated for room %s (mark-all-read)",
                    room_id,
                )

        return Response({"message": "Room marked as read", "updated_count": len(unread_ids)})


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
            admin_qs = Ir.objects.filter(ir_access_level=AccessLevel.ADMIN, status=True)
            candidates_qs = (base_qs | upline_qs | admin_qs).distinct()
        elif requester.ir_access_level == AccessLevel.LDC:
            # Use subtree directly rather than get_viewable_irs() to avoid queryset
            # union chaining issues; then add upline contacts.
            downline_qs = requester.get_subtree_irs().filter(status=True).exclude(ir_id=requester.ir_id)
            upline_qs = _get_upline_irs(requester, [AccessLevel.CTC])
            admin_qs = Ir.objects.filter(ir_access_level=AccessLevel.ADMIN, status=True)
            candidates_qs = (downline_qs | upline_qs | admin_qs).distinct()
        elif requester.ir_access_level == AccessLevel.CTC:
            viewable_ids = list(requester.get_viewable_irs().filter(status=True).exclude(ir_id=requester.ir_id).values_list("ir_id", flat=True))
            admin_qs = Ir.objects.filter(ir_access_level=AccessLevel.ADMIN, status=True)
            candidates_qs = (Ir.objects.filter(ir_id__in=viewable_ids) | admin_qs).distinct()
        elif requester.ir_access_level == AccessLevel.GC:
            downline_qs = requester.get_subtree_irs().filter(status=True).exclude(ir_id=requester.ir_id)
            upline_qs = _get_upline_irs(requester, [AccessLevel.LDC])
            candidates_qs = (downline_qs | upline_qs).distinct()
        else:
            candidates_qs = base_qs

        if query:
            candidates_qs = candidates_qs.filter(
                Q(ir_name__icontains=query) | Q(display_name__icontains=query) | Q(ir_id__icontains=query)
            )

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
                        "ir_name": c.chat_name,
                        "avatar_url": c.avatar_url,
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

        member_ir_ids = _room_member_ir_ids(room.id)
        room.delete()
        invalidate_chat_rooms_cache(member_ir_ids)
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
        invalidate_chat_rooms_cache(_room_member_ir_ids(room.id))

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
        invalidate_chat_rooms_cache(_room_member_ir_ids(room.id))

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
        invalidate_chat_rooms_cache(_room_member_ir_ids(room.id))

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
        invalidate_chat_rooms_cache(_room_member_ir_ids(room.id))

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


def _save_chat_attachment(uploaded, *, folder, allowed_mimes, max_size):
    """
    Shared MIME-check + storage-save logic used by both the chat message
    upload endpoint and the sticker upload endpoint. Returns (url, mime, error).
    """
    if not uploaded:
        return None, None, "file is required"

    mime = uploaded.content_type or mimetypes.guess_type(uploaded.name)[0] or "application/octet-stream"
    if mime not in allowed_mimes:
        return None, mime, f"File type '{mime}' is not allowed"

    if uploaded.size > max_size:
        return None, mime, f"File exceeds {max_size // (1024 * 1024)} MB limit"

    ext = os.path.splitext(uploaded.name)[1].lower()
    file_bytes = uploaded.read()

    if mime in _VOICE_TYPES and mime not in SKIP_TRANSCODE_MIMES:
        # Normalize every voice note to AAC/m4a so it plays on Safari/iOS
        # regardless of which browser recorded it (see
        # core/utils/audio_transcode.py). Falls back to the original upload
        # untouched if transcoding fails for any reason — never blocks the send.
        transcoded = transcode_voice_note(file_bytes)
        if transcoded is not None:
            file_bytes = transcoded
            ext = ".m4a"

    filename = f"{folder}/{uuid.uuid4().hex}{ext}"
    saved_path = default_storage.save(filename, ContentFile(file_bytes))
    # default_storage.url() returns the Cloudinary CDN URL in production,
    # or a local /media/ path in development — works for both.
    return default_storage.url(saved_path), mime, None


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
        file_url, mime, error = _save_chat_attachment(
            uploaded,
            folder=f"chat/{room_id}",
            allowed_mimes=_IMAGE_TYPES | _VIDEO_TYPES | _VOICE_TYPES | _FILE_TYPES,
            max_size=500 * 1024 * 1024,  # 500 MB — kept in lockstep with DATA_UPLOAD_MAX_MEMORY_SIZE in settings.py
        )
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

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
        invalidate_chat_rooms_cache(_room_member_ir_ids(room.id))

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
        invalidate_chat_rooms_cache(_room_member_ir_ids(room.id))

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
        invalidate_chat_rooms_cache(_room_member_ir_ids(room.id))

        system_message = ChatMessage.objects.create(
            room=room,
            sender=requester,
            message_type=ChatMessageType.SYSTEM,
            content=f"{requester.chat_name} changed the group photo",
        )
        serialized_system_message = _serialize_message(system_message)

        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"chat_room_{room_id}",
                {"type": "room_updated", "room": {"id": room.id, "room_name": room.room_name, "image_url": image_url}},
            )
            async_to_sync(channel_layer.group_send)(
                f"chat_room_{room_id}",
                {"type": "message_created", "message": serialized_system_message},
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to broadcast room_updated/message_created for room %s (image change)", room_id
            )

        return Response({"image_url": image_url, "system_message": serialized_system_message})


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
                    "avatar_url": r.reader.avatar_url,
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
            logging.getLogger(__name__).exception(
                "Failed to broadcast reaction_updated for message %s (add)", message_id
            )

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
            logging.getLogger(__name__).exception(
                "Failed to broadcast reaction_updated for message %s (remove)", message_id
            )

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


class IrAvatarUpload(APIView):
    """Upload or update a user's own profile picture, shown across chats."""

    def post(self, request, ir_id):
        requester = _get_ir(ir_id)
        if not requester:
            return Response({"detail": "Invalid IR ID"}, status=status.HTTP_400_BAD_REQUEST)

        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        mime = uploaded.content_type or ""
        if mime not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
            return Response({"detail": "Only image files are allowed"}, status=status.HTTP_400_BAD_REQUEST)

        if uploaded.size > 5 * 1024 * 1024:
            return Response({"detail": "Image must be under 5 MB"}, status=status.HTTP_400_BAD_REQUEST)

        ext = os.path.splitext(uploaded.name)[1].lower() or ".jpg"
        filename = f"avatars/{ir_id}/{uuid.uuid4().hex}{ext}"
        saved_path = default_storage.save(filename, ContentFile(uploaded.read()))
        avatar_url = default_storage.url(saved_path)

        requester.avatar_url = avatar_url
        requester.save(update_fields=["avatar_url"])

        return Response({"avatar_url": avatar_url})

    def delete(self, request, ir_id):
        requester = _get_ir(ir_id)
        if not requester:
            return Response({"detail": "Invalid IR ID"}, status=status.HTTP_400_BAD_REQUEST)

        requester.avatar_url = None
        requester.save(update_fields=["avatar_url"])
        return Response({"message": "Profile picture removed"})


class ChatTabsConfigView(APIView):
    """Per-user chat tab configuration — custom tabs, assignments, order, hidden defaults."""

    def get(self, request):
        ir_id = request.GET.get("ir_id")
        ir = _get_ir(ir_id)
        if not ir:
            return Response({"detail": "ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            cfg = ir.chat_tabs_config
            return Response({"config": cfg.config})
        except ChatTabsConfig.DoesNotExist:
            return Response({"config": {}})

    def put(self, request):
        ir_id = request.data.get("ir_id")
        config = request.data.get("config")
        if not isinstance(config, dict):
            return Response({"detail": "config must be an object"}, status=status.HTTP_400_BAD_REQUEST)
        ir = _get_ir(ir_id)
        if not ir:
            return Response({"detail": "ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
        ChatTabsConfig.objects.update_or_create(ir=ir, defaults={"config": config})
        return Response({"config": config})


class ChatMessageSearch(APIView):
    """Search messages across all rooms or within a specific room."""

    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        room_id = request.GET.get("room_id")
        query = (request.GET.get("q") or "").strip()
        limit = min(int(request.GET.get("limit", 30)), 100)

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
        if not query:
            return Response({"detail": "q is required"}, status=status.HTTP_400_BAD_REQUEST)

        if room_id:
            room = get_object_or_404(ChatRoom, id=room_id)
            if not _is_room_member(room, requester):
                return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)
            qs = ChatMessage.objects.filter(room_id=room_id)
        else:
            member_room_ids = ChatRoomMember.objects.filter(ir=requester).values_list("room_id", flat=True)
            qs = ChatMessage.objects.filter(room_id__in=member_room_ids)

        qs = (
            qs.filter(is_deleted=False, content__icontains=query)
            .select_related("sender", "room")
            .order_by("-created_at")[:limit]
        )

        results = []
        for msg in qs:
            results.append({
                "id": msg.id,
                "room_id": msg.room_id,
                "room_name": msg.room.room_name,
                "sender_ir_id": msg.sender.ir_id,
                "sender_name": msg.sender.chat_name,
                "content": msg.content,
                "message_type": msg.message_type,
                "created_at": msg.created_at.isoformat(),
            })

        return Response({"results": results, "count": len(results)})


class ChatRoomMediaGallery(APIView):
    """Return all media attachments for a room, grouped by type."""

    def get(self, request, room_id):
        requester_ir_id = request.GET.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        if not _is_room_member(room, requester):
            return Response({"detail": "Not authorized for this room"}, status=status.HTTP_403_FORBIDDEN)

        media_types = [ChatMessageType.IMAGE, ChatMessageType.VIDEO, ChatMessageType.FILE, ChatMessageType.VOICE]
        qs = (
            ChatMessage.objects.filter(room=room, message_type__in=media_types, is_deleted=False)
            .select_related("sender")
            .order_by("-created_at")
        )

        grouped = {"images": [], "videos": [], "files": [], "voice": []}
        for msg in qs:
            item = {
                "id": msg.id,
                "sender_name": msg.sender.chat_name,
                "attachment_url": msg.attachment_url,
                "attachment_name": msg.attachment_name,
                "attachment_size": msg.attachment_size,
                "attachment_duration": msg.attachment_duration,
                "created_at": msg.created_at.isoformat(),
            }
            if msg.message_type == ChatMessageType.IMAGE:
                grouped["images"].append(item)
            elif msg.message_type == ChatMessageType.VIDEO:
                grouped["videos"].append(item)
            elif msg.message_type == ChatMessageType.FILE:
                grouped["files"].append(item)
            elif msg.message_type == ChatMessageType.VOICE:
                grouped["voice"].append(item)

        return Response({"media": grouped})


class ChatRoomMute(APIView):
    """Mute or unmute a room for the requesting user."""

    def post(self, request, room_id):
        requester_ir_id = request.data.get("requester_ir_id")
        duration = request.data.get("duration")  # "1h", "8h", "forever", or null to unmute

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        room = get_object_or_404(ChatRoom, id=room_id)
        membership = ChatRoomMember.objects.filter(room=room, ir=requester).first()
        if not membership:
            return Response({"detail": "Not a member of this room"}, status=status.HTTP_403_FORBIDDEN)

        if not duration:
            membership.is_muted = False
            membership.muted_until = None
        else:
            membership.is_muted = True
            if duration == "forever":
                membership.muted_until = None
            elif duration == "8h":
                membership.muted_until = timezone.now() + timedelta(hours=8)
            else:
                membership.muted_until = timezone.now() + timedelta(hours=1)

        membership.save(update_fields=["is_muted", "muted_until"])
        invalidate_chat_rooms_cache([requester.ir_id])

        return Response({
            "is_muted": membership.is_muted,
            "muted_until": membership.muted_until.isoformat() if membership.muted_until else None,
        })


class ChatPresenceUpdate(APIView):
    """Update last_seen timestamp for a user (called on app focus/blur)."""

    def post(self, request):
        ir_id = request.data.get("ir_id")
        ir = _get_ir(ir_id)
        if not ir:
            return Response({"detail": "ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        ir.last_seen = timezone.now()
        ir.save(update_fields=["last_seen"])
        return Response({"last_seen": ir.last_seen.isoformat()})


# ── Sticker packs ──────────────────────────────────────────────────────────

_STICKER_TYPES = _IMAGE_TYPES | _VIDEO_TYPES
_STICKER_MAX_SIZE = 5 * 1024 * 1024  # 5 MB — stickers should be small


def _serialize_sticker(sticker):
    return {
        "id": sticker.id,
        "pack_id": sticker.pack_id,
        "image_url": sticker.image_url,
        "is_animated": sticker.is_animated,
        "emoji": sticker.emoji,
        "keywords": sticker.keywords,
    }


def _serialize_sticker_pack(pack, stickers=None, *, is_own=None, requester=None):
    stickers = stickers if stickers is not None else list(pack.stickers.all())
    if is_own is None:
        is_own = bool(requester) and pack.owner_id == requester.ir_id
    return {
        "id": pack.id,
        "name": pack.name,
        "cover_sticker_id": pack.cover_sticker_id,
        "is_public": pack.is_public,
        "is_own": is_own,
        "stickers": [_serialize_sticker(s) for s in stickers],
    }


class StickerPackListCreate(APIView):
    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        packs = (
            StickerPack.objects.filter(Q(owner=requester) | Q(subscriptions__ir=requester))
            .distinct()
            .prefetch_related("stickers")
        )
        return Response({
            "packs": [_serialize_sticker_pack(pack, requester=requester) for pack in packs]
        })

    def post(self, request):
        requester_ir_id = request.data.get("requester_ir_id")
        name = (request.data.get("name") or "").strip()

        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)
        if not name:
            return Response({"detail": "name is required"}, status=status.HTTP_400_BAD_REQUEST)

        pack = StickerPack.objects.create(owner=requester, name=name)
        return Response(
            {"pack": _serialize_sticker_pack(pack, stickers=[], is_own=True)},
            status=status.HTTP_201_CREATED,
        )


class StickerPackDelete(APIView):
    def delete(self, request, pack_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        pack = get_object_or_404(StickerPack, id=pack_id)
        if pack.owner_id != requester.ir_id:
            return Response({"detail": "Not authorized to delete this pack"}, status=status.HTTP_403_FORBIDDEN)

        pack.delete()
        return Response({"message": "Pack deleted", "pack_id": pack_id})


class StickerUpload(APIView):
    """Upload a sticker image/video into a pack the requester owns."""

    def post(self, request, pack_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        pack = get_object_or_404(StickerPack, id=pack_id)
        if pack.owner_id != requester.ir_id:
            return Response({"detail": "Not authorized to add to this pack"}, status=status.HTTP_403_FORBIDDEN)

        uploaded = request.FILES.get("file")
        file_url, mime, error = _save_chat_attachment(
            uploaded,
            folder=f"stickers/{pack_id}",
            allowed_mimes=_STICKER_TYPES,
            max_size=_STICKER_MAX_SIZE,
        )
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        sticker = Sticker.objects.create(
            pack=pack,
            image_url=file_url,
            is_animated=mime in _VIDEO_TYPES,
            emoji=(request.data.get("emoji") or "").strip(),
            keywords=(request.data.get("keywords") or "").strip(),
            order=pack.stickers.count(),
        )
        if not pack.cover_sticker_id:
            pack.cover_sticker = sticker
            pack.save(update_fields=["cover_sticker"])

        return Response({"sticker": _serialize_sticker(sticker)}, status=status.HTTP_201_CREATED)


class StickerDelete(APIView):
    def delete(self, request, pack_id, sticker_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        pack = get_object_or_404(StickerPack, id=pack_id)
        if pack.owner_id != requester.ir_id:
            return Response({"detail": "Not authorized to modify this pack"}, status=status.HTTP_403_FORBIDDEN)

        sticker = get_object_or_404(Sticker, id=sticker_id, pack=pack)
        sticker.delete()
        return Response({"message": "Sticker deleted", "sticker_id": sticker_id})


class StickerPackBrowse(APIView):
    """Public packs the requester doesn't already own or have subscribed to."""

    def get(self, request):
        requester_ir_id = request.GET.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        packs = (
            StickerPack.objects.filter(is_public=True)
            .exclude(owner=requester)
            .exclude(subscriptions__ir=requester)
            .prefetch_related("stickers")
        )
        return Response({
            "packs": [_serialize_sticker_pack(pack, is_own=False) for pack in packs]
        })


class StickerPackSubscribe(APIView):
    """Add a public pack to the requester's own sticker picker without copying it."""

    def post(self, request, pack_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        pack = get_object_or_404(StickerPack, id=pack_id)
        if not pack.is_public:
            return Response({"detail": "This pack is not public"}, status=status.HTTP_400_BAD_REQUEST)

        StickerPackSubscription.objects.get_or_create(ir=requester, pack=pack)
        return Response({"pack": _serialize_sticker_pack(pack, is_own=False)})


class StickerPackUnsubscribe(APIView):
    """Remove a subscribed (non-owned) pack from the requester's picker. Never touches the pack itself."""

    def post(self, request, pack_id):
        requester_ir_id = request.data.get("requester_ir_id")
        requester = _get_ir(requester_ir_id)
        if not requester:
            return Response({"detail": "requester_ir_id is invalid"}, status=status.HTTP_400_BAD_REQUEST)

        StickerPackSubscription.objects.filter(ir=requester, pack_id=pack_id).delete()
        return Response({"message": "Unsubscribed", "pack_id": pack_id})
