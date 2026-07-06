from unittest.mock import MagicMock, patch

from django.test import TestCase, TransactionTestCase
from firebase_admin import exceptions as fb_exceptions
from firebase_admin.messaging import UnregisteredError

from core.chat.consumers import ChatConsumer
from core.models import AccessLevel, ChatRoom, ChatRoomMember, ChatRoomType, Ir
from core.utils.firebase_messaging import is_dead_token_error, send_multicast


def _fake_send_response(success, code=None, message="", exception_type=None):
    resp = MagicMock()
    resp.success = success
    if success:
        resp.exception = None
    else:
        # A real exception instance, not MagicMock — send_multicast keys
        # dead-token detection off type(err).__name__, and reassigning
        # `__class__` on a MagicMock fools isinstance() but NOT type(), so a
        # mock can't fake a specific exception class name for this purpose.
        exc_cls = type(exception_type or "FirebaseError", (Exception,), {})
        exc = exc_cls(message)
        exc.code = code
        resp.exception = exc
    return resp


class IsDeadTokenErrorTests(TestCase):
    """
    These use the actual firebase_admin exception shapes (verified against
    the installed SDK version), not the legacy FCM HTTP API's error codes
    (e.g. 'registration-token-not-registered') — the Admin SDK's v1 API never
    produces those, which is exactly why the original version of this check
    silently never matched real failures and dead tokens were never pruned.
    """

    def test_unregistered_error_is_dead(self):
        self.assertTrue(is_dead_token_error(None, None, exception_type="UnregisteredError"))
        # Real SDK instance, for good measure — confirms .code and message shape.
        exc = UnregisteredError("Device unregistered.")
        self.assertEqual(exc.code, fb_exceptions.NOT_FOUND)
        self.assertTrue(is_dead_token_error(exc.code, str(exc), exception_type=type(exc).__name__))

    def test_known_dead_codes_are_dead(self):
        self.assertTrue(is_dead_token_error("NOT_FOUND", ""))
        self.assertTrue(is_dead_token_error("INVALID_ARGUMENT", ""))

    def test_dead_message_text_without_code_is_dead(self):
        self.assertTrue(is_dead_token_error(None, "Requested entity was not found: Device unregistered."))

    def test_unrelated_error_is_not_dead(self):
        self.assertFalse(is_dead_token_error("UNAVAILABLE", "Some transient failure"))


class SendMulticastBatchApiTests(TestCase):
    """
    send_multicast must use the Admin SDK's batch API (send_each_for_multicast)
    rather than a per-token loop, and must preserve the {success, failure,
    failure_details} shape the rest of the codebase relies on.
    """

    @patch("core.utils.firebase_messaging.firebase_initialized", True)
    @patch("core.utils.firebase_messaging.messaging")
    def test_success_and_failure_counts(self, mock_messaging):
        mock_messaging.MulticastMessage.return_value = MagicMock()
        mock_messaging.Notification.return_value = MagicMock()
        mock_messaging.WebpushConfig.return_value = MagicMock()
        mock_messaging.WebpushNotification.return_value = MagicMock()

        responses = [
            _fake_send_response(True),
            _fake_send_response(
                False, code="NOT_FOUND", message="Device unregistered.", exception_type="UnregisteredError"
            ),
        ]
        batch_response = MagicMock()
        batch_response.responses = responses
        batch_response.success_count = 1
        batch_response.failure_count = 1
        mock_messaging.send_each_for_multicast.return_value = batch_response

        result = send_multicast(
            fcm_tokens=["token-good-aaaaaaaaaaaaaaaa", "token-bad-aaaaaaaaaaaaaaaaa"],
            title="Someone",
            body="hi",
        )

        mock_messaging.send_each_for_multicast.assert_called_once()
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["failure"], 1)
        self.assertEqual(len(result["failure_details"]), 1)
        self.assertEqual(result["failure_details"][0]["token"], "token-bad-aaaaaaaaaaaaaaaaa")
        # send_multicast must capture the exception's class name — it's the
        # signal is_dead_token_error actually relies on for real SDK failures.
        self.assertEqual(result["failure_details"][0]["exception_type"], "UnregisteredError")
        self.assertTrue(is_dead_token_error(
            result["failure_details"][0]["code"],
            result["failure_details"][0]["message"],
            exception_type=result["failure_details"][0]["exception_type"],
        ))

    @patch("core.utils.firebase_messaging.firebase_initialized", True)
    @patch("core.utils.firebase_messaging.messaging")
    def test_no_valid_tokens_short_circuits_without_calling_sdk(self, mock_messaging):
        result = send_multicast(fcm_tokens=["", None], title="t", body="b")
        mock_messaging.send_each_for_multicast.assert_not_called()
        self.assertEqual(result, {"success": 0, "failure": 0})


class NotifyRoomMembersPruningTests(TransactionTestCase):
    """
    _notify_room_members (core/chat/consumers.py) must prune a dead FCM
    token from the owning member's Ir row after a failed send — the gap the
    chat push path used to have relative to the generic notification path.

    Uses TransactionTestCase (not TestCase) because database_sync_to_async
    hops to a different thread for DB access, which is incompatible with
    TestCase's single-connection-per-test atomic-transaction wrapping.
    """

    async def test_dead_token_is_pruned_after_failed_send(self):
        from channels.db import database_sync_to_async

        sender = await database_sync_to_async(Ir.objects.create)(
            ir_id="SENDER1", ir_name="Sender", ir_access_level=AccessLevel.LS, status=True
        )
        recipient = await database_sync_to_async(Ir.objects.create)(
            ir_id="RECIP1", ir_name="Recipient", ir_access_level=AccessLevel.LS, status=True,
            fcm_tokens=["dead-token-aaaaaaaaaaaaaaaa", "live-token-bbbbbbbbbbbbbbbb"],
        )
        room = await database_sync_to_async(ChatRoom.objects.create)(
            room_type=ChatRoomType.GROUP, room_name="Room", created_by=sender
        )
        await database_sync_to_async(ChatRoomMember.objects.create)(room=room, ir=sender)
        await database_sync_to_async(ChatRoomMember.objects.create)(room=room, ir=recipient)

        fake_result = {
            "success": 1,
            "failure": 1,
            "failure_details": [
                {
                    "index": 0,
                    "token": "dead-token-aaaaaaaaaaaaaaaa",
                    "code": "NOT_FOUND",
                    "message": "Device unregistered.",
                    "exception_type": "UnregisteredError",
                }
            ],
        }

        consumer = ChatConsumer()
        with patch("core.utils.firebase_messaging.send_multicast", return_value=fake_result):
            await consumer._notify_room_members(room.id, sender.ir_id, sender.chat_name, "hello")

        await database_sync_to_async(recipient.refresh_from_db)()
        self.assertEqual(recipient.fcm_tokens, ["live-token-bbbbbbbbbbbbbbbb"])
