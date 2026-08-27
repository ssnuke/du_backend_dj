from django.test import TestCase, override_settings
from core.models import Ir, Team, TeamMember, AccessLevel, TeamRole


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class BoardRankBadgeTests(TestCase):
    """
    The board draws a rank badge per row. It used to send no access level at
    all, so the client defaulted to "LDC" and every row claimed to be an LDC —
    a CTC whose own header read CTC still showed as LDC on their card.
    """

    @classmethod
    def setUpTestData(cls):
        cls.seq = 0

        def mk(name, lvl=AccessLevel.IR, parent=None):
            cls.seq += 1
            return Ir.objects.create(ir_id=f"R{cls.seq:06d}", ir_name=name,
                                     ir_email=f"r{cls.seq}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True, parent_ir=parent)

        cls.admin = mk("The Admin", AccessLevel.ADMIN)
        # A CTC who runs a team of their own — this is the case that misreported.
        cls.ctc = mk("Raasitha the CTC", AccessLevel.CTC, parent=cls.admin)
        cls.ldc = mk("A real LDC", AccessLevel.LDC, parent=cls.admin)

        for person in (cls.ctc, cls.ldc):
            team = Team.objects.create(name=f"{person.ir_name} team", created_by=person)
            TeamMember.objects.create(team=team, ir=person, role=TeamRole.LDC)
            TeamMember.objects.create(team=team, ir=mk(f"member of {person.ir_name}", parent=person),
                                      role=TeamRole.IR)

    def groups(self, requester):
        r = self.client.get("/api/manager_dashboard/", {"requester_ir_id": requester.ir_id})
        self.assertEqual(r.status_code, 200, r.content[:200])
        return {g["label"]: g for g in r.json()["groups"]}

    def test_every_row_carries_an_access_level(self):
        for g in self.groups(self.admin).values():
            self.assertIn("access_level", g)

    def test_a_ctc_is_reported_as_a_ctc_not_an_ldc(self):
        g = self.groups(self.admin)["Raasitha the CTC"]
        self.assertEqual(g["access_level"], AccessLevel.CTC)

    def test_an_ldc_is_still_reported_as_an_ldc(self):
        g = self.groups(self.admin)["A real LDC"]
        self.assertEqual(g["access_level"], AccessLevel.LDC)

    def test_a_merged_group_reports_the_most_senior_rank(self):
        """
        Customize can merge several people into one row. There is no single
        rank then, so the most senior one present speaks for the row rather
        than whichever happened to be listed first.
        """
        r = self.client.put(
            f"/api/dashboard_mapping/{self.admin.ir_id}/",
            data={"config": {
                "groups": [{"id": "merged", "label": "Merged row",
                            # LDC listed FIRST on purpose: taking the first
                            # member's rank would report LDC here.
                            "member_ldc_ids": [self.ldc.ir_id, self.ctc.ir_id]}],
                "known_ldc_ids": [self.ldc.ir_id, self.ctc.ir_id],
            }},
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content[:200])
        g = self.groups(self.admin)["Merged row"]
        self.assertEqual(g["access_level"], AccessLevel.CTC, "CTC (2) outranks LDC (3)")
