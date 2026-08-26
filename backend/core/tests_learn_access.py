from django.test import TestCase, override_settings
from core.models import Ir, AccessLevel, LearnVideo


LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class GrowthBitesAccessTests(TestCase):
    """
    Growth Bites is for LS and above. Hiding the tab in the client is not the
    control: the list endpoint would still ship the videos, and the stream
    endpoint takes an id, so a restricted video would stay playable by anyone
    who kept or guessed one.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl):
            return Ir.objects.create(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)

        cls.admin = mk("LADMIN", AccessLevel.ADMIN)
        cls.ctc = mk("LCTC", AccessLevel.CTC)
        cls.ldc = mk("LLDC", AccessLevel.LDC)
        cls.ls = mk("LLS", AccessLevel.LS)
        cls.gc = mk("LGC", AccessLevel.GC)
        cls.ir = mk("LIR", AccessLevel.IR)

        def vid(title, cat, bid):
            return LearnVideo.objects.create(title=title, category=cat, bunny_video_id=bid,
                                             bunny_library_id="lib", is_published=True)

        cls.basic = vid("Basic one", "basic_training", "b1")
        cls.blocks = vid("Blocks one", "building_blocks", "b2")
        cls.growth = vid("Growth one", "growth_bites", "b3")

    def titles_for(self, ir):
        r = self.client.get(f"/api/learn/{ir.ir_id}/videos/")
        self.assertEqual(r.status_code, 200, r.content)
        return {v["title"] for v in r.json()}

    def test_ls_and_above_see_growth_bites(self):
        for ir in (self.admin, self.ctc, self.ldc, self.ls):
            self.assertIn("Growth one", self.titles_for(ir), f"{ir.ir_id} should see it")

    def test_gc_and_ir_do_not(self):
        for ir in (self.gc, self.ir):
            self.assertNotIn("Growth one", self.titles_for(ir), f"{ir.ir_id} must not see it")

    def test_the_other_categories_are_untouched(self):
        """The gate must not narrow what everyone could already see."""
        for ir in (self.gc, self.ir):
            self.assertEqual(self.titles_for(ir), {"Basic one", "Blocks one"})

    def test_stream_url_is_refused_to_a_gc(self):
        r = self.client.get(f"/api/learn/{self.gc.ir_id}/videos/{self.growth.id}/stream/")
        self.assertEqual(r.status_code, 404)

    def test_stream_url_is_granted_to_an_ls(self):
        r = self.client.get(f"/api/learn/{self.ls.ir_id}/videos/{self.growth.id}/stream/")
        self.assertEqual(r.status_code, 200, r.content)

    def test_an_ir_can_still_stream_an_unrestricted_video(self):
        r = self.client.get(f"/api/learn/{self.ir.ir_id}/videos/{self.basic.id}/stream/")
        self.assertEqual(r.status_code, 200, r.content)
