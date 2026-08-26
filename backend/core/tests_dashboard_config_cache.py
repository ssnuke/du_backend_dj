from django.test import TestCase, override_settings
from core.models import Ir, Team, TeamMember, AccessLevel, TeamRole


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class ManagerDashboardConfigCacheTests(TestCase):
    """
    The board is cached, and its content depends on the requester's saved
    grouping. Saving in Customize and refetching used to return the previous
    grouping from cache, so the board didn't show the LDC you just added or
    removed until the TTL lapsed.
    """

    @classmethod
    def setUpTestData(cls):
        cls.seq = 0

        def mk(name, lvl=AccessLevel.IR, parent=None):
            cls.seq += 1
            return Ir.objects.create(ir_id=f"C{cls.seq:06d}", ir_name=name,
                                     ir_email=f"c{cls.seq}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True, parent_ir=parent)

        cls.ctc = mk("The CTC", AccessLevel.CTC)
        cls.ldc_a = mk("LDC A", AccessLevel.LDC, parent=cls.ctc)
        cls.ldc_b = mk("LDC B", AccessLevel.LDC, parent=cls.ctc)
        for ldc in (cls.ldc_a, cls.ldc_b):
            team = Team.objects.create(name=f"{ldc.ir_name} team", created_by=ldc)
            # An LDC is recognised by having an LDC-role team membership.
            TeamMember.objects.create(team=team, ir=ldc, role=TeamRole.LDC)
            TeamMember.objects.create(team=team, ir=mk(f"member of {ldc.ir_name}", parent=ldc),
                                      role=TeamRole.IR)

    def board_labels(self):
        r = self.client.get("/api/manager_dashboard/", {"requester_ir_id": self.ctc.ir_id})
        self.assertEqual(r.status_code, 200, r.content[:200])
        return [g["label"] or g["member_ldc_ids"][0] for g in r.json()["groups"]]

    def save_config(self, member_ids):
        r = self.client.put(
            f"/api/dashboard_mapping/{self.ctc.ir_id}/",
            data={"config": {
                "groups": [{"id": m, "label": None, "member_ldc_ids": [m]} for m in member_ids],
                "known_ldc_ids": [self.ldc_a.ir_id, self.ldc_b.ir_id],
            }},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content[:200])

    def test_removing_an_ldc_shows_immediately(self):
        both = self.board_labels()
        self.assertEqual(len(both), 2)

        # Warm the cache, then exclude B — the board must drop it at once.
        self.board_labels()
        self.save_config([self.ldc_a.ir_id])
        after = self.board_labels()
        self.assertEqual(len(after), 1, "board still showed the removed LDC from cache")

    def test_adding_an_ldc_back_shows_immediately(self):
        self.save_config([self.ldc_a.ir_id])
        self.assertEqual(len(self.board_labels()), 1)

        self.save_config([self.ldc_a.ir_id, self.ldc_b.ir_id])
        self.assertEqual(len(self.board_labels()), 2, "board did not pick up the re-added LDC")

    def test_repeat_fetches_are_still_cached_when_nothing_changed(self):
        """The fix must not defeat caching outright."""
        from django.core.cache import cache
        self.board_labels()
        keys_before = len(getattr(cache, "_cache", {}))
        self.board_labels()
        self.assertEqual(len(getattr(cache, "_cache", {})), keys_before,
                         "a second identical fetch created a new cache entry")
