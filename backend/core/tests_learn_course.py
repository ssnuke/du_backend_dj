from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir, LearnVideo, LearnVideoProgress

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM, BUNNY_STREAM_CDN_HOSTNAME="cdn.test", BUNNY_STREAM_TOKEN_KEY="")
class LearnCourseTests(TestCase):
    """
    Basic Training is a course for a Player: each lesson opens when the one
    before it (that has a video) is finished. Anyone else has it all open.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl):
            return Ir.objects.create(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)

        cls.player = mk("LPLAYER", AccessLevel.PLAYER)
        cls.ir = mk("LIR", AccessLevel.IR)

        def vid(key, title):
            return LearnVideo.objects.create(title=title, lesson_key=key, bunny_video_id=f"b-{title}",
                                             bunny_library_id="lib", category="basic_training")
        cls.welcome = vid("welcome", "Welcome")
        cls.why = vid("why_nm", "Why NM")
        # switch_on has no video: coming soon.
        cls.info = vid("info_part_1", "Info part")

    def course(self, who):
        r = self.client.get(f"/api/learn/{who.ir_id}/videos/?course=1")
        self.assertEqual(r.status_code, 200, r.content)
        return [i for i in r.json() if i["category"] == "basic_training"]

    def test_an_app_that_does_not_ask_for_the_course_gets_the_plain_list(self):
        """The previous app version must see no placeholders and no course ordering."""
        r = self.client.get("/api/learn/LIR/videos/")
        self.assertEqual(r.status_code, 200)
        items = r.json()
        self.assertEqual({i["title"] for i in items}, {"Welcome", "Why NM", "Info part"})
        self.assertTrue(all(i["id"] is not None for i in items), "no Coming-soon placeholders")
        self.assertFalse(any("coming_soon" in i for i in items))

    def test_outline_is_numbered_with_coming_soon_slots(self):
        items = self.course(self.player)
        self.assertEqual([i["lesson_number"] for i in items][:4], [1, 2, 3, 4])
        self.assertEqual([i["title"] for i in items][:4], ["Welcome", "Why NM", "Switch ON", "OH1"])
        self.assertTrue(items[2]["coming_soon"])
        self.assertIsNone(items[2]["id"])
        self.assertEqual(len(items), 10)

    def test_the_outline_is_the_agreed_ten_lessons_in_order_with_oh1_before_info(self):
        from core.learn_curriculum import LESSONS
        self.assertEqual(
            [l["title"] for l in LESSONS],
            ["Welcome", "Why NM", "Switch ON", "OH1", "Info Part 1", "Info Part 2",
             "Scripts", "Closings", "Prospecting", "Success Formula"],
        )
        # There is no OH2 any more.
        self.assertNotIn("oh2", [l["key"] for l in LESSONS])
        items = self.course(self.player)
        self.assertEqual([i["lesson_number"] for i in items], list(range(1, 11)))

    def test_scripts_lesson_is_a_reference_not_a_coming_soon_video(self):
        items = {i["lesson_key"]: i for i in self.course(self.player)}
        scripts = items["scripts"]
        self.assertTrue(scripts["reference"])
        self.assertFalse(scripts["coming_soon"], "it has no video and is not 'coming soon'")
        self.assertIsNone(scripts["id"])
        self.assertEqual(set(scripts["scripts"]), {"info", "dkd", "drilling", "purpose-call"})

    def test_reference_lesson_is_locked_until_earlier_lessons_but_never_holds_anyone_up(self):
        # Before anything is finished the scripts are locked like any later lesson.
        self.assertTrue({i["lesson_key"]: i for i in self.course(self.player)}["scripts"]["locked"])
        closings = LearnVideo.objects.create(title="Closings", lesson_key="closings", bunny_video_id="c1",
                                             bunny_library_id="l", category="basic_training")
        for v in (self.welcome, self.why, self.info):
            self.client.post(f"/api/learn/LPLAYER/videos/{v.id}/complete/")
        items = {i["lesson_key"]: i for i in self.course(self.player)}
        self.assertFalse(items["scripts"]["locked"], "open once the videos before it are done")
        # Nothing waits on it: Closings (after Scripts) is open without any "completion" of Scripts.
        self.assertFalse(items["closings"]["locked"])
        self.assertEqual(self.client.get(f"/api/learn/LPLAYER/videos/{closings.id}/stream/").status_code, 200)

    def test_an_ir_sees_the_reference_lesson_open(self):
        self.assertFalse({i["lesson_key"]: i for i in self.course(self.ir)}["scripts"]["locked"])

    def test_both_info_parts_offer_the_info_script(self):
        from core.learn_curriculum import LESSONS
        by_key = {l["key"]: l for l in LESSONS}
        self.assertEqual(by_key["info_part_1"]["scripts"], ["info"])
        self.assertEqual(by_key["info_part_2"]["scripts"], ["info"])

    def test_player_only_has_first_lesson_open(self):
        items = {i["title"]: i for i in self.course(self.player)}
        self.assertFalse(items["Welcome"]["locked"])
        self.assertTrue(items["Why NM"]["locked"])
        self.assertTrue(items["Info part"]["locked"])

    def test_finishing_a_lesson_opens_the_next_and_coming_soon_is_skipped(self):
        r = self.client.post(f"/api/learn/LPLAYER/videos/{self.welcome.id}/complete/")
        self.assertEqual(r.status_code, 200, r.content)
        self.client.post(f"/api/learn/LPLAYER/videos/{self.why.id}/complete/")
        items = {i["title"]: i for i in self.course(self.player)}
        self.assertTrue(items["Welcome"]["completed"])
        # Switch ON has no video, so Info part opens straight after Why NM.
        self.assertFalse(items["Info part"]["locked"])

    def test_a_finished_lesson_never_locks_when_the_outline_changes(self):
        """A video added to an earlier slot later must not lock lessons already finished."""
        for v in (self.welcome, self.why, self.info):
            self.client.post(f"/api/learn/LPLAYER/videos/{v.id}/complete/")
        # A new, unfinished video appears in Info Part 2, before OH1.
        LearnVideo.objects.create(title="Info 2", lesson_key="info_part_2", bunny_video_id="n2",
                                  bunny_library_id="l", category="basic_training")
        oh1 = LearnVideo.objects.create(title="OH1 video", lesson_key="oh1", bunny_video_id="n3",
                                        bunny_library_id="l", category="basic_training")
        LearnVideoProgress.objects.create(ir_id="LPLAYER", video=oh1, completed_at="2026-01-01T00:00:00Z")
        items = {i["title"]: i for i in self.course(self.player)}
        self.assertFalse(items["OH1 video"]["locked"], "already finished, so it stays open")
        self.assertFalse(items["Info 2"]["locked"], "the next lesson to do is open")

    def test_locked_lesson_cannot_be_streamed_or_completed(self):
        self.assertEqual(self.client.get(f"/api/learn/LPLAYER/videos/{self.why.id}/stream/").status_code, 403)
        self.assertEqual(self.client.post(f"/api/learn/LPLAYER/videos/{self.why.id}/complete/").status_code, 403)
        self.assertEqual(LearnVideoProgress.objects.count(), 0)
        self.assertEqual(self.client.get(f"/api/learn/LPLAYER/videos/{self.welcome.id}/stream/").status_code, 200)

    def test_ir_has_everything_open(self):
        for item in self.course(self.ir):
            self.assertFalse(item["locked"])
        self.assertEqual(self.client.get(f"/api/learn/LIR/videos/{self.info.id}/stream/").status_code, 200)

    def test_promotion_opens_everything_without_losing_progress(self):
        self.client.post(f"/api/learn/LPLAYER/videos/{self.welcome.id}/complete/")
        Ir.objects.filter(pk="LPLAYER").update(ir_access_level=AccessLevel.IR)
        items = {i["title"]: i for i in self.course(self.player)}
        self.assertTrue(items["Welcome"]["completed"])
        self.assertFalse(items["Info part"]["locked"])

    def test_unassigned_videos_wait_for_the_whole_course_for_a_player(self):
        extra = LearnVideo.objects.create(title="Bonus", bunny_video_id="b-bonus", bunny_library_id="lib",
                                          category="basic_training")
        self.assertEqual(self.client.get(f"/api/learn/LPLAYER/videos/{extra.id}/stream/").status_code, 403)
        for v in (self.welcome, self.why, self.info):
            self.client.post(f"/api/learn/LPLAYER/videos/{v.id}/complete/")
        self.assertEqual(self.client.get(f"/api/learn/LPLAYER/videos/{extra.id}/stream/").status_code, 200)

    def test_lesson_script_materials_are_listed(self):
        items = {i["title"]: i for i in self.course(self.player)}
        self.assertEqual(items["Info part"]["scripts"], ["info"])

    def test_assign_command_matches_titles_and_never_guesses(self):
        LearnVideo.objects.all().delete()
        for t in ["1. Welcome", "Why N.M", "Prospecting tips", "Info part 2", "Info part", "Scripts", "Random bonus"]:
            LearnVideo.objects.create(title=t, bunny_video_id=f"x-{t}", bunny_library_id="l", category="basic_training")
        call_command("assign_learn_lessons", stdout=StringIO())
        self.assertEqual(LearnVideo.objects.exclude(lesson_key__isnull=True).count(), 0, "dry run")
        call_command("assign_learn_lessons", "--apply", stdout=StringIO())
        got = {v.title: v.lesson_key for v in LearnVideo.objects.all()}
        self.assertEqual(got["1. Welcome"], "welcome")
        self.assertEqual(got["Why N.M"], "why_nm")
        self.assertEqual(got["Prospecting tips"], "prospecting")
        self.assertIsNone(got["Random bonus"])
        self.assertEqual(got["Info part 2"], "info_part_2")
        self.assertIsNone(got["Info part"], "a bare Info part could be 1 or 2, so it is not guessed")
        self.assertIsNone(got["Scripts"], "Scripts is a reference lesson and takes no video")


@override_settings(CACHES=LOCMEM, BUNNY_STREAM_CDN_HOSTNAME="cdn.test", BUNNY_STREAM_TOKEN_KEY="")
class LearnResumeTests(TestCase):
    """Where someone stopped is remembered, so they can carry on from there."""

    @classmethod
    def setUpTestData(cls):
        cls.player = Ir.objects.create(ir_id="RPLAYER", ir_name="P", ir_email="p@t.t", ir_password="x",
                                       ir_access_level=AccessLevel.PLAYER, status=True)
        cls.one = LearnVideo.objects.create(title="Welcome", lesson_key="welcome", bunny_video_id="r1",
                                            bunny_library_id="l", category="basic_training", duration_seconds=600)
        cls.two = LearnVideo.objects.create(title="Why NM", lesson_key="why_nm", bunny_video_id="r2",
                                            bunny_library_id="l", category="basic_training", duration_seconds=600)
        cls.bonus = LearnVideo.objects.create(title="Growth", bunny_video_id="r3", bunny_library_id="l",
                                              category="building_blocks", duration_seconds=300)

    def save(self, video, seconds, who="RPLAYER"):
        return self.client.post(f"/api/learn/{who}/videos/{video.id}/progress/",
                                data={"position_seconds": seconds}, content_type="application/json")

    def resume(self, title):
        items = self.client.get("/api/learn/RPLAYER/videos/?course=1").json()
        return next(i for i in items if i["title"] == title)["resume_seconds"]

    def test_position_is_saved_and_returned(self):
        self.assertEqual(self.resume("Welcome"), 0)
        self.assertEqual(self.save(self.one, 125.7).status_code, 200)
        self.assertEqual(self.resume("Welcome"), 125)
        self.save(self.one, 240)
        self.assertEqual(self.resume("Welcome"), 240)

    def test_finishing_clears_the_position(self):
        self.save(self.one, 590)
        self.client.post(f"/api/learn/RPLAYER/videos/{self.one.id}/complete/")
        self.assertEqual(self.resume("Welcome"), 0)
        # A late ping after completion must not bring the resume point back.
        self.save(self.one, 598)
        self.assertEqual(self.resume("Welcome"), 0)

    def test_cannot_save_progress_on_a_locked_lesson(self):
        self.assertEqual(self.save(self.two, 30).status_code, 403)
        self.assertEqual(LearnVideoProgress.objects.filter(video=self.two).count(), 0)

    def test_position_is_validated_and_capped_at_the_duration(self):
        self.assertEqual(self.save(self.one, -5).status_code, 400)
        self.assertEqual(self.save(self.one, "abc").status_code, 400)
        self.save(self.one, 99999)
        self.assertEqual(self.resume("Welcome"), 600)

    def test_resume_works_outside_the_course_and_zero_clears_it(self):
        self.save(self.bonus, 100)
        self.assertEqual(self.resume("Growth"), 100)
        self.save(self.bonus, 0)
        self.assertEqual(self.resume("Growth"), 0)

    def test_resume_is_per_person(self):
        Ir.objects.create(ir_id="ROTHER", ir_name="O", ir_email="o@t.t", ir_password="x",
                          ir_access_level=AccessLevel.IR, status=True)
        self.save(self.one, 200)
        items = self.client.get("/api/learn/ROTHER/videos/?course=1").json()
        self.assertEqual(next(i for i in items if i["title"] == "Welcome")["resume_seconds"], 0)


@override_settings(CACHES=LOCMEM)
class CourseOutlineAdminTests(TestCase):
    """The Django admin screen that places videos into the course."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User
        cls.staff = User.objects.create_superuser("boss", "b@t.t", "pw")
        make = lambda t: LearnVideo.objects.create(title=t, bunny_video_id=f"a-{t}", bunny_library_id="l",
                                                   category="basic_training")
        cls.a, cls.b, cls.c = make("Alpha"), make("Beta"), make("Gamma")

    def setUp(self):
        self.client.force_login(self.staff)

    def test_page_lists_all_ten_lessons(self):
        r = self.client.get("/admin/core/learnvideo/course/")
        self.assertEqual(r.status_code, 200)
        for title in ("Welcome", "Why NM", "Switch ON", "Success Formula"):
            self.assertContains(r, title)

    def test_scripts_lesson_has_no_video_dropdown_and_ignores_posts(self):
        r = self.client.get("/admin/core/learnvideo/course/")
        self.assertNotContains(r, 'name="slot_scripts"')
        self.assertContains(r, "Scripts only")
        self.client.post("/admin/core/learnvideo/course/", {"slot_scripts": self.a.id, "slot_welcome": self.a.id})
        self.assertEqual(LearnVideo.objects.get(pk=self.a.pk).lesson_key, "welcome")

    def test_changelist_links_to_the_outline(self):
        self.assertContains(self.client.get("/admin/core/learnvideo/"), "Course outline")

    def test_saving_assigns_and_clears_slots(self):
        self.client.post("/admin/core/learnvideo/course/", {"slot_welcome": self.a.id, "slot_why_nm": self.b.id})
        self.assertEqual(LearnVideo.objects.get(pk=self.a.pk).lesson_key, "welcome")
        self.assertEqual(LearnVideo.objects.get(pk=self.b.pk).lesson_key, "why_nm")
        # Swap, and free a slot, in one save — the unique key must not collide.
        self.client.post("/admin/core/learnvideo/course/", {"slot_welcome": self.b.id, "slot_closings": self.a.id})
        self.assertEqual(LearnVideo.objects.get(pk=self.b.pk).lesson_key, "welcome")
        self.assertEqual(LearnVideo.objects.get(pk=self.a.pk).lesson_key, "closings")
        self.assertIsNone(LearnVideo.objects.get(pk=self.c.pk).lesson_key)

    def test_one_video_cannot_fill_two_lessons(self):
        self.client.post("/admin/core/learnvideo/course/", {"slot_welcome": self.a.id})
        self.client.post("/admin/core/learnvideo/course/", {"slot_welcome": self.b.id, "slot_why_nm": self.b.id})
        # Rejected as a whole: the earlier assignment is untouched.
        self.assertEqual(LearnVideo.objects.get(pk=self.a.pk).lesson_key, "welcome")
        self.assertIsNone(LearnVideo.objects.get(pk=self.b.pk).lesson_key)

    def test_non_staff_cannot_open_it(self):
        self.client.logout()
        r = self.client.get("/admin/core/learnvideo/course/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])


@override_settings(CACHES=LOCMEM)
class ScriptsMigrationTests(TestCase):
    def test_a_video_placed_in_scripts_is_unassigned(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module("core.migrations.0067_scripts_lesson_is_reference")
        v = LearnVideo.objects.create(title="S", lesson_key="scripts", bunny_video_id="s1",
                                      bunny_library_id="l", category="basic_training")
        migration.unassign_scripts(apps, None)
        self.assertIsNone(LearnVideo.objects.get(pk=v.pk).lesson_key)


@override_settings(CACHES=LOCMEM)
class OutlineMigrationTests(TestCase):
    """Videos already placed when the outline changed keep their place where they can."""

    def test_old_assignments_carry_over(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module("core.migrations.0066_course_outline_info_parts")

        def make(title, key):
            return LearnVideo.objects.create(title=title, lesson_key=key, bunny_video_id=f"m-{title}",
                                             bunny_library_id="l", category="basic_training")
        info = make("Info", "info_part")
        oh2 = make("Second OH", "oh2")
        oh1 = make("First OH", "oh1")
        migration.carry_over_assignments(apps, None)
        self.assertEqual(LearnVideo.objects.get(pk=info.pk).lesson_key, "info_part_1")
        self.assertIsNone(LearnVideo.objects.get(pk=oh2.pk).lesson_key)
        self.assertEqual(LearnVideo.objects.get(pk=oh1.pk).lesson_key, "oh1")
