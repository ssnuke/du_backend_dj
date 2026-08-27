import json
from django.test import TestCase, override_settings
from core.models import Ir, Team, TeamMember, TeamRole, AccessLevel

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class IrStatusManagementScopeTests(TestCase):
    """
    Activating/deactivating an IR is open to ADMIN, CTC and LDC. Both gates
    used to be an exact match on LDC, so a CTC could not see the section and
    would have been refused by the server even if they had.

    The list and the toggle share one scope helper on purpose: this screen has
    already had a bug where the two drifted and an LDC saw somebody in the
    list who could not actually be toggled.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True,
                                     parent_ir=parent)

        cls.admin = mk("SADMIN", "The Admin", AccessLevel.ADMIN)
        cls.ctc = mk("SCTC", "The CTC", AccessLevel.CTC, parent=cls.admin)
        cls.ldc = mk("SLDC", "The LDC", AccessLevel.LDC, parent=cls.ctc)
        cls.member = mk("SMEM", "Team Member", AccessLevel.IR, parent=cls.ldc)
        cls.ls = mk("SLS", "An LS", AccessLevel.LS, parent=cls.ldc)

        # Someone in an unrelated branch — nobody above should reach them.
        cls.other_ctc = mk("SOCTC", "Other CTC", AccessLevel.CTC, parent=cls.admin)
        cls.outsider = mk("SOUT", "Outsider", AccessLevel.IR, parent=cls.other_ctc)

        team = Team.objects.create(name="The Team", created_by=cls.ldc)
        TeamMember.objects.create(team=team, ir=cls.ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=team, ir=cls.member, role=TeamRole.IR)
        TeamMember.objects.create(team=team, ir=cls.ls, role=TeamRole.IR)

    def listing(self, requester, **params):
        r = self.client.get(f"/api/ir_status_management/{requester.ir_id}/", params)
        return r

    def listed_ids(self, requester):
        r = self.listing(requester, limit=100)
        self.assertEqual(r.status_code, 200, r.content[:200])
        return {x["ir_id"] for x in r.json()["results"]}

    def toggle(self, requester, target, value=False):
        return self.client.patch(
            f"/api/ir/{target.ir_id}/status/",
            data=json.dumps({"requester_ir_id": requester.ir_id, "status": value}),
            content_type="application/json",
        )

    # ── who can reach the screen ─────────────────────────────────────────
    def test_admin_ctc_and_ldc_can_all_open_the_list(self):
        for who in (self.admin, self.ctc, self.ldc):
            self.assertEqual(self.listing(who).status_code, 200, f"{who.ir_id} should be allowed")

    def test_ls_and_below_still_cannot(self):
        for who in (self.ls, self.member):
            self.assertEqual(self.listing(who).status_code, 403, f"{who.ir_id} must be refused")

    def test_an_ls_cannot_toggle_anyone(self):
        self.assertEqual(self.toggle(self.ls, self.member).status_code, 403)

    # ── the fix: a CTC can actually do it ────────────────────────────────
    def test_a_ctc_can_deactivate_someone_in_their_downline(self):
        r = self.toggle(self.ctc, self.member, False)
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.member.refresh_from_db()
        self.assertFalse(self.member.status)

    def test_an_admin_can_too(self):
        r = self.toggle(self.admin, self.member, False)
        self.assertEqual(r.status_code, 200, r.content[:200])

    def test_an_ldc_still_can(self):
        r = self.toggle(self.ldc, self.member, False)
        self.assertEqual(r.status_code, 200, r.content[:200])

    def test_reactivating_works_too(self):
        self.toggle(self.ctc, self.member, False)
        self.assertEqual(self.toggle(self.ctc, self.member, True).status_code, 200)
        self.member.refresh_from_db()
        self.assertTrue(self.member.status)

    # ── scope is still enforced ──────────────────────────────────────────
    def test_a_ctc_cannot_reach_another_branch(self):
        self.assertEqual(self.toggle(self.ctc, self.outsider).status_code, 403)
        self.outsider.refresh_from_db()
        self.assertTrue(self.outsider.status)

    def test_nobody_can_deactivate_themselves(self):
        for who in (self.admin, self.ctc, self.ldc):
            r = self.toggle(who, who)
            self.assertEqual(r.status_code, 403, f"{who.ir_id} must not deactivate themselves")

    def test_the_requester_never_appears_in_their_own_list(self):
        for who in (self.admin, self.ctc, self.ldc):
            self.assertNotIn(who.ir_id, self.listed_ids(who))

    # ── the list and the toggle must agree ───────────────────────────────
    def test_everything_listed_can_actually_be_toggled(self):
        """
        The bug this screen already had once. Whatever the list offers, the
        toggle must accept — for every role, not just LDC.
        """
        for who in (self.admin, self.ctc, self.ldc):
            for ir_id in self.listed_ids(who):
                target = Ir.objects.get(ir_id=ir_id)
                r = self.toggle(who, target, True)
                self.assertEqual(r.status_code, 200,
                                 f"{who.ir_id} was shown {ir_id} but refused on toggle")

    def test_an_ldc_sees_their_team_and_a_ctc_sees_their_downline(self):
        self.assertEqual(self.listed_ids(self.ldc), {self.member.ir_id, self.ls.ir_id})
        ctc_ids = self.listed_ids(self.ctc)
        self.assertIn(self.ldc.ir_id, ctc_ids)
        self.assertIn(self.member.ir_id, ctc_ids)
        self.assertNotIn(self.outsider.ir_id, ctc_ids, "other branch must stay out")

    def test_search_still_narrows_the_list(self):
        r = self.listing(self.ctc, search="Team Member", limit=100)
        self.assertEqual(r.status_code, 200)
        self.assertEqual({x["ir_id"] for x in r.json()["results"]}, {self.member.ir_id})
