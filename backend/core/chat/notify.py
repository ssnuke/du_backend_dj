"""
Shared, synchronous chat push-notification logic used by both the WebSocket
consumer (core/chat/consumers.py, wrapped in database_sync_to_async /
run_in_executor since it must not block the event loop) and the plain REST
message endpoint (core/views/chat.py ChatRoomMessages.post, which handles
attachments/stickers/GIFs/forwards — messages sent this way previously never
triggered a push at all).
"""
import logging

from django.core.cache import cache
from django.utils import timezone

from core.models import ChatMessageType, ChatRoom, ChatRoomMember

logger = logging.getLogger(__name__)

# ── Room presence ────────────────────────────────────────────────────────────
# Tracks which (room_id, ir_id) pairs currently have a live ChatConsumer
# connection, so push notifications can skip members who are already looking
# at the room (in this tab or another tab/device). Backed by the same Redis
# cache as everything else, with a short TTL refreshed on every WS heartbeat
# ping (chatService.js pings every 25s) — so a connection that dies without a
# clean disconnect (tab crash, laptop sleep, dropped network) self-expires
# quickly instead of permanently silencing pushes for that member.
_PRESENCE_TTL = 75  # seconds — 3x the 25s client heartbeat interval


def _presence_key(room_id, ir_id):
    return f"chat_presence:{room_id}:{ir_id}"


def mark_room_present(room_id, ir_id):
    cache.set(_presence_key(room_id, ir_id), True, _PRESENCE_TTL)


def clear_room_present(room_id, ir_id):
    cache.delete(_presence_key(room_id, ir_id))


def present_ir_ids(room_id, ir_ids):
    """
    Batched presence lookup for a whole room's membership at once — one Redis
    round-trip (cache.get_many, which django-redis implements via MGET)
    instead of one GET per member. Matters for larger rooms: gathering
    notification targets used to do a sequential per-member presence check,
    so a 20+ person broadcast room paid 20+ round-trips before a single push
    could go out.
    """
    if not ir_ids:
        return set()
    keys = {_presence_key(room_id, ir_id): ir_id for ir_id in ir_ids}
    found = cache.get_many(list(keys.keys()))
    return {keys[key] for key in found}

_PREVIEW_LABELS = {
    ChatMessageType.IMAGE: "📷 Photo",
    ChatMessageType.VIDEO: "🎥 Video",
    ChatMessageType.VOICE: "🎤 Voice message",
    ChatMessageType.STICKER: "🎨 Sticker",
    ChatMessageType.GIF: "🎞️ GIF",
}


def preview_for_message(content, message_type, attachment_name=None):
    if message_type == ChatMessageType.FILE:
        return f"📎 {attachment_name or 'File'}"
    if message_type in _PREVIEW_LABELS:
        return _PREVIEW_LABELS[message_type]
    text = content or ""
    return text if len(text) <= 100 else text[:100] + "…"


def validate_mentioned_ir_ids(room_id, sender_ir_id, candidate_ir_ids):
    """
    Filter client-supplied @mention ir_ids down to actual current members of
    the room, excluding the sender — never trust the client list directly,
    since it drives a mute bypass and a distinct "mentioned you" push below.
    """
    if not candidate_ir_ids:
        return set()
    return set(
        ChatRoomMember.objects.filter(room_id=room_id, ir_id__in=candidate_ir_ids)
        .exclude(ir_id=sender_ir_id)
        .values_list("ir_id", flat=True)
    )


def gather_notification_targets(room_id, sender_ir_id, mentioned_ir_ids=None):
    """
    Return (room_type, room_name, tokens, token_owner, mentioned_tokens) for
    FCM push to every room member except the sender, skipping members
    currently muted for this room — unless they were @mentioned, which
    bypasses mute the same way most chat apps treat a direct mention.
    token_owner maps token -> ir_id, used to prune dead tokens after send.
    mentioned_tokens is the subset of tokens belonging to mentioned members,
    so the caller can send them a distinct "mentioned you" notification.
    """
    mentioned_ir_ids = set(mentioned_ir_ids or ())
    room = ChatRoom.objects.get(id=room_id)
    now = timezone.now()

    # Bulk-clear expired mutes up front instead of a per-member .save() loop.
    ChatRoomMember.objects.filter(
        room_id=room_id, is_muted=True, muted_until__lte=now
    ).update(is_muted=False, muted_until=None)

    members = list(
        ChatRoomMember.objects.filter(room_id=room_id)
        .exclude(ir__ir_id=sender_ir_id)
        .select_related("ir")
    )
    present = present_ir_ids(room_id, [m.ir_id for m in members])

    token_owner = {}
    mentioned_tokens = set()
    for member in members:
        is_mentioned = member.ir_id in mentioned_ir_ids
        if member.is_muted and not is_mentioned and (member.muted_until is None or member.muted_until > now):
            continue
        # Skip members who currently have this room open (this tab or another
        # tab/device) — they're already seeing the message live over the
        # WebSocket, a push on top of that is just noise.
        if member.ir_id in present:
            continue
        ir = member.ir
        if ir.fcm_tokens and isinstance(ir.fcm_tokens, list):
            valid = [t for t in ir.fcm_tokens if t and isinstance(t, str) and len(t) > 10]
            for token in valid:
                token_owner[token] = ir.ir_id
                if is_mentioned:
                    mentioned_tokens.add(token)

    return room.room_type, room.room_name, list(token_owner.keys()), token_owner, mentioned_tokens


def build_notification_batches(tokens, mentioned_tokens, sender_name, room_type, room_name):
    """
    Split targets into (batch_tokens, title) groups: mentioned members get a
    distinct "{sender} mentioned you" title, everyone else gets the existing
    generic title. Pure — no I/O — so it's safe to call from either the sync
    view path or inside the WS consumer's network-only executor.
    """
    regular_tokens = [t for t in tokens if t not in mentioned_tokens]
    batches = []
    if regular_tokens:
        title = sender_name if room_type == "direct" else f"{sender_name} · {room_name}"
        batches.append((regular_tokens, title))
    if mentioned_tokens:
        batches.append((list(mentioned_tokens), f"{sender_name} mentioned you"))
    return batches


def prune_dead_tokens(bad_tokens_by_ir_id):
    """Remove permanently-invalid FCM tokens from the owning Ir rows."""
    from core.models import Ir

    for ir_id, tokens in bad_tokens_by_ir_id.items():
        ir = Ir.objects.filter(ir_id=ir_id).first()
        if not ir or not ir.fcm_tokens:
            continue
        new_tokens = [t for t in ir.fcm_tokens if t not in tokens]
        if len(new_tokens) != len(ir.fcm_tokens):
            ir.fcm_tokens = new_tokens
            ir.save(update_fields=["fcm_tokens"])


def notify_chat_room_members(
    room_id, sender_ir_id, sender_name, content, message_type="text", attachment_name=None, mentioned_ir_ids=None
):
    """
    Synchronous end-to-end push: gather targets, send (one batch per
    mentioned/non-mentioned group — see build_notification_batches), prune
    dead tokens. Safe to call directly from a normal (sync) Django view.
    Callers running inside an asyncio event loop (the WS consumer) should
    instead reuse gather_notification_targets/build_notification_batches/
    prune_dead_tokens directly and run the network send in an executor — see
    core/chat/consumers.py.
    """
    from core.utils.firebase_messaging import is_dead_token_error, send_multicast

    room_type, room_name, tokens, token_owner, mentioned_tokens = gather_notification_targets(
        room_id, sender_ir_id, mentioned_ir_ids
    )
    if not tokens:
        return

    preview = preview_for_message(content, message_type, attachment_name)
    batches = build_notification_batches(tokens, mentioned_tokens, sender_name, room_type, room_name)

    bad_tokens_by_ir_id = {}
    for batch_tokens, title in batches:
        result = send_multicast(
            fcm_tokens=batch_tokens,
            title=title,
            body=preview,
            data={"notification_type": "chat_message", "room_id": str(room_id)},
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
        prune_dead_tokens(bad_tokens_by_ir_id)
