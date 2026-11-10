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
            items = self.client.get(f"/api/learn/{who.ir_id}/videos/?audio=1").json()
            row = next(i for i in items if i["id"] == self.audio.id)
            self.assertEqual((row["media_type"], row["category"]), ("audio", "start_your_day"))
            self.assertEqual(row["duration_seconds"], 2246)
            self.assertIn("cover.jpg", row["thumbnail_url"])
            video_row = next(i for i in items if i["id"] == self.video.id)
            self.assertEqual(video_row["media_type"], "video")

    def test_an_older_app_is_not_sent_audio_it_cannot_play(self):
        for url in (f"/api/learn/{self.ir.ir_id}/videos/", f"/api/learn/{self.ir.ir_id}/videos/?course=1"):
            ids = {i.get("id") for i in self.client.get(url).json()}
            self.assertNotIn(self.audio.id, ids, url)
            self.assertIn(self.video.id, ids, url)

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


class AudioContentTypeTests(TestCase):
    def test_the_type_follows_the_file_extension(self):
        from core.utils.private_storage import audio_content_type
        self.assertEqual(audio_content_type("learn-audio/a.mp3"), "audio/mpeg")
        self.assertEqual(audio_content_type("learn-audio/a.M4A"), "audio/mp4")
        self.assertEqual(audio_content_type("learn-audio/a.unknown"), "audio/mpeg")


class LearnAudioAdminTests(TestCase):
    """The Django admin can add an audio item without any Bunny ids."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.admin_user = get_user_model().objects.create_superuser("tadmin", "a@t.t", "pw12345!")
        self.client.force_login(self.admin_user)

    def test_the_add_form_offers_the_audio_fields(self):
        html = self.client.get("/admin/core/learnvideo/add/").content.decode()
        self.assertIn('name="media_type"', html)
        self.assertIn('name="audio_key"', html)

    def test_saving_an_audio_item_from_the_form_works_without_bunny_ids(self):
        r = self.client.post("/admin/core/learnvideo/add/", {
            "title": "Morning", "description": "", "category": "start_your_day", "lesson_key": "", "order": "0",
            "is_published": "on", "media_type": "audio", "audio_key": "learn-audio/morning.mp3",
            "bunny_video_id": "", "bunny_library_id": "", "thumbnail_url": "", "duration_seconds": "600",
        })
        self.assertEqual(r.status_code, 302, getattr(r, "content", b"")[:300])
        item = LearnVideo.objects.get(title="Morning")
        self.assertEqual((item.media_type, item.audio_key), ("audio", "learn-audio/morning.mp3"))
        self.assertTrue(item.bunny_video_id.startswith("audio-"))


class AudioLessonInTheCourseTests(TestCase):
    """An audio lesson takes its place in Basic Training like any other lesson."""

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl):
            return Ir.objects.create(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True)
        cls.player = mk("ACPLAYER", AccessLevel.PLAYER)
        mkv = lambda t, key, **kw: LearnVideo.objects.create(
            title=t, lesson_key=key, category="basic_training", bunny_video_id=f"b-{key}", bunny_library_id="1", **kw)
        cls.info2 = mkv("Info Part 2", "info_part_2")
        cls.audio = LearnVideo.objects.create(
            title="Five + Three", lesson_key="five_plus_three", category="basic_training", media_type="audio",
            audio_key="learn-audio/five-plus-three.m4a", duration_seconds=4449)
        cls.closings = mkv("Closings", "closings")
        # everything before Info Part 2 is a Coming-soon slot, so it holds nobody up

    def course(self):
        items = self.client.get("/api/learn/ACPLAYER/videos/?course=1&audio=1").json()
        return {i["lesson_key"]: i for i in items if i.get("lesson_key")}

    def test_it_sits_right_after_info_part_2_and_before_scripts(self):
        keys = [i["lesson_key"] for i in self.client.get("/api/learn/ACPLAYER/videos/?course=1&audio=1").json()
                if i.get("lesson_key")]
        self.assertEqual(keys[keys.index("info_part_2") + 1], "five_plus_three")
        self.assertEqual(keys[keys.index("five_plus_three") + 1], "scripts")

    def test_a_player_must_finish_it_to_unlock_what_follows(self):
        c = self.course()
        self.assertEqual(c["five_plus_three"]["media_type"], "audio")
        self.assertTrue(c["five_plus_three"]["locked"], "after Info Part 2, which is unfinished")
        self.client.post(f"/api/learn/ACPLAYER/videos/{self.info2.id}/complete/")
        c = self.course()
        self.assertFalse(c["five_plus_three"]["locked"])
        self.assertTrue(c["closings"]["locked"], "Closings waits for the audio lesson")
        r = self.client.post(f"/api/learn/ACPLAYER/videos/{self.audio.id}/complete/")
        self.assertEqual(r.status_code, 200, r.content)
        c = self.course()
        self.assertTrue(c["five_plus_three"]["completed"])
        self.assertFalse(c["closings"]["locked"])

    def test_a_locked_audio_lesson_cannot_be_streamed(self):
        r = self.client.get(f"/api/learn/ACPLAYER/videos/{self.audio.id}/stream/")
        self.assertEqual(r.status_code, 403)
