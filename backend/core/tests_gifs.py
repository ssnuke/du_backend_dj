from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from core.models import AccessLevel, ChatMessageType, ChatRoom, ChatRoomMember, ChatRoomType, Ir


def _fake_giphy_response(gif_ids):
    data = []
    for gif_id in gif_ids:
        data.append({
            "id": gif_id,
            "title": f"gif {gif_id}",
            "images": {
                "original": {"url": f"https://media.giphy.com/{gif_id}/giphy.gif", "mp4": f"https://media.giphy.com/{gif_id}/giphy.mp4"},
                "fixed_width": {"url": f"https://media.giphy.com/{gif_id}/200.gif", "webp": f"https://media.giphy.com/{gif_id}/200.webp"},
            },
        })
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"data": data}
    return resp


@override_settings(GIPHY_API_KEY="test-key")
class GifSearchTests(TestCase):
    @patch("core.views.gifs.requests.get")
    def test_search_normalizes_response(self, mock_get):
        mock_get.return_value = _fake_giphy_response(["abc123"])

        resp = self.client.get("/api/gifs/search/", {"q": "cat"})
        self.assertEqual(resp.status_code, 200)
        gifs = resp.json()["gifs"]
        self.assertEqual(len(gifs), 1)
        self.assertEqual(gifs[0]["id"], "abc123")
        self.assertEqual(gifs[0]["full_url"], "https://media.giphy.com/abc123/giphy.mp4")
        self.assertEqual(gifs[0]["preview_url"], "https://media.giphy.com/abc123/200.webp")

        # requester query forwarded to GIPHY with our API key
        called_params = mock_get.call_args.kwargs["params"]
        self.assertEqual(called_params["q"], "cat")
        self.assertEqual(called_params["api_key"], "test-key")

    def test_search_requires_query(self):
        resp = self.client.get("/api/gifs/search/")
        self.assertEqual(resp.status_code, 400)

    @patch("core.views.gifs.requests.get")
    def test_search_handles_upstream_failure_gracefully(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.Timeout("giphy took too long")

        resp = self.client.get("/api/gifs/search/", {"q": "cat"})
        self.assertEqual(resp.status_code, 502)

    @override_settings(GIPHY_API_KEY="")
    def test_search_without_configured_key(self):
        resp = self.client.get("/api/gifs/search/", {"q": "cat"})
        self.assertEqual(resp.status_code, 502)


@override_settings(GIPHY_API_KEY="test-key")
class GifTrendingTests(TestCase):
    @patch("core.views.gifs.requests.get")
    def test_trending_normalizes_response(self, mock_get):
        mock_get.return_value = _fake_giphy_response(["trend1", "trend2"])

        resp = self.client.get("/api/gifs/trending/")
        self.assertEqual(resp.status_code, 200)
        gifs = resp.json()["gifs"]
        self.assertEqual([g["id"] for g in gifs], ["trend1", "trend2"])

        endpoint_called = mock_get.call_args.args[0]
        self.assertIn("/trending", endpoint_called)


class GifMessageTypeTests(TestCase):
    def setUp(self):
        self.sender = Ir.objects.create(
            ir_id="GIFSENDER1", ir_name="Sender", ir_access_level=AccessLevel.LS, status=True
        )
        self.room = ChatRoom.objects.create(
            room_type=ChatRoomType.GROUP, room_name="Room", created_by=self.sender
        )
        ChatRoomMember.objects.create(room=self.room, ir=self.sender)

    def test_sending_a_gif_message_is_accepted(self):
        resp = self.client.post(
            f"/api/chat_rooms/{self.room.id}/messages/",
            {
                "requester_ir_id": self.sender.ir_id,
                "message_type": "gif",
                "attachment_url": "https://media.giphy.com/abc123/giphy.mp4",
                "attachment_name": "funny gif",
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        message = resp.json()["chat_message"]
        self.assertEqual(message["message_type"], ChatMessageType.GIF)
        self.assertEqual(message["attachment_url"], "https://media.giphy.com/abc123/giphy.mp4")
