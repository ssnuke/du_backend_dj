from unittest import mock

from django.test import TestCase

from core.models import AccessLevel, Ir, LearnVideo


class LearnAudioTests(TestCase):
    """An audio training sits in Learn beside the videos and plays from a signed file link."""

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl):
            return Ir.objects.create(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True)
        cls.player = mk("LAPLAYER", AccessLevel.PLAYER)
        cls.ir = mk("LAIR", AccessLevel.IR)
        cls.audio = LearnVideo.objects.create(
            title="Start Your Day", category="start_your_day", media_type="audio",
            audio_key="learn-audio/morning.mp3", duration_seconds=2246,
            thumbnail_url="https://example.com/cover.jpg",
        )
        cls.video = LearnVideo.objects.create(
            title="Welcome", category="basic_training", bunny_video_id="vid-1", bunny_library_id="9")

    def test_an_audio_item_needs_no_bunny_ids(self):
        self.assertTrue(self.audio.bunny_video_id.startswith("audio-"))
        second = LearnVideo.objects.create(title="Another", category="start_your_day", media_type="audio",
                                           audio_key="learn-audio/b.mp3")
        self.assertNotEqual(second.bunny_video_id, self.audio.bunny_video_id)
        self.assertEqual(LearnVideo.objects.get(pk=self.video.pk).media_type, "video", "videos are unchanged")

    def test_everyone_including_a_player_sees_it_in_the_list(self):
        for who in (self.player, self.ir):
            items = self.client.get(f"/api/learn/{who.ir_id}/videos/").json()
            row = next(i for i in items if i["id"] == self.audio.id)
            self.assertEqual((row["media_type"], row["category"]), ("audio", "start_your_day"))
            self.assertEqual(row["duration_seconds"], 2246)
            self.assertIn("cover.jpg", row["thumbnail_url"])
            video_row = next(i for i in items if i["id"] == self.video.id)
            self.assertEqual(video_row["media_type"], "video")

    def test_the_stream_is_a_signed_link_to_the_file(self):
        with mock.patch("core.views.learn.private_storage.presign_stream", return_value="https://r2.example/signed") as p:
            r = self.client.get(f"/api/learn/{self.player.ir_id}/videos/{self.audio.id}/stream/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["stream_url"], "https://r2.example/signed")
        self.assertEqual(r.json()["media_type"], "audio")
        p.assert_called_once_with("learn-audio/morning.mp3")

    def test_storage_not_set_up_is_reported_not_crashed(self):
        from core.utils.private_storage import StorageNotConfigured
        with mock.patch("core.views.learn.private_storage.presign_stream", side_effect=StorageNotConfigured()):
            r = self.client.get(f"/api/learn/{self.ir.ir_id}/videos/{self.audio.id}/stream/")
        self.assertEqual(r.status_code, 503)

    def test_an_audio_item_with_no_file_set_is_not_found(self):
        LearnVideo.objects.filter(pk=self.audio.pk).update(audio_key="")
        r = self.client.get(f"/api/learn/{self.ir.ir_id}/videos/{self.audio.id}/stream/")
        self.assertEqual(r.status_code, 404)

    def test_bunny_videos_still_stream_from_bunny(self):
        r = self.client.get(f"/api/learn/{self.ir.ir_id}/videos/{self.video.id}/stream/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("playlist.m3u8", r.json()["stream_url"])
