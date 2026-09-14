from django.test import TestCase, override_settings

from core.models import (Ir, Team, TeamMember, TeamRole, AccessLevel,
                         Pocket, PocketMember)

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class EditableIrsForPipelineTests(TestCase):
    """
    The Pipeline Tracker picker used to be populated from a VIEW permission
    (get_viewable_irs_for_name_list) while saving is gated by a narrower EDIT
    permission (can_add_data_for_ir). For an LDC those two already disagreed
    in production: their view reaches the whole hierarchy subtree, but their
    edit permission is members of teams THEY created. Picking someone in the
    subtree but outside those teams showed their stats and then refused the
    save with no warning up front. The same gap would hit a non-pocket-head
    GC the moment Settings started offering them this screen — which is why
    this got fixed alongside letting GC in.
    """

    def mk(self, ir_id, name, lvl, parent=None):
        return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                 ir_password="x", ir_access_level=lvl, status=True,
                                 parent_ir=parent)

    def editable(self, requester):
        return set(requester.get_editable_irs_for_pipeline().values_list("ir_id", flat=True))

    def viewable(self, requester):
        return set(requester.get_viewable_irs_for_name_list().values_list("ir_id", flat=True))

    # ── the LDC gap, already live before this change ───────────────────────
    def test_an_ldc_can_edit_members_of_teams_they_created(self):
        ldc = self.mk("PELDC", "LDC", AccessLevel.LDC)
        member = self.mk("PEMEM", "In their team", AccessLevel.IR, parent=ldc)
        team = Team.objects.create(name="Created by LDC", created_by=ldc)
        TeamMember.objects.create(team=team, ir=ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=team, ir=member, role=TeamRole.IR)
        self.assertIn(member.ir_id, self.editable(ldc))

    def test_an_ldc_cannot_edit_a_downline_outside_their_own_teams(self):
        """
        The exact gap: this person is in the LDC's hierarchy subtree (so
        VIEW allows them) but not in a team the LDC created (so EDIT does
        not) — the picker must not offer them.
        """
        ldc = self.mk("PELDC2", "LDC", AccessLevel.LDC)
        deep = self.mk("PEDEEP", "In subtree only", AccessLevel.IR, parent=ldc)
        self.assertIn(deep.ir_id, self.viewable(ldc), "sanity: VIEW does reach them")
        self.assertNotIn(deep.ir_id, self.editable(ldc), "EDIT must not offer them")

    # ── the GC gap, newly relevant now that GC reaches this screen ─────────
    def test_a_pocket_head_gc_can_edit_their_downline(self):
        gc = self.mk("PEGCHEAD", "Pocket head GC", AccessLevel.GC)
        downline = self.mk("PEGCKID", "Under the pocket head", AccessLevel.IR, parent=gc)
        team = Team.objects.create(name="T", created_by=gc)
        pocket = Pocket.objects.create(team=team, name="P", created_by=gc)
        PocketMember.objects.create(pocket=pocket, ir=gc, role=TeamRole.GC, is_head=True)
        self.assertIn(downline.ir_id, self.editable(gc))

    def test_a_non_pocket_head_gc_cannot_edit_their_downline(self):
        """
        The gap this session's Settings change would otherwise have exposed:
        a GC with a genuine downline (a personal referral) who isn't a
        pocket head could view that person's stats but never save for them.
        """
        gc = self.mk("PEGCPLAIN", "Plain GC", AccessLevel.GC)
        downline = self.mk("PEGCKID2", "A referral", AccessLevel.IR, parent=gc)
        self.assertIn(downline.ir_id, self.viewable(gc), "sanity: VIEW does reach them")
        self.assertNotIn(downline.ir_id, self.editable(gc), "EDIT must not offer them")

    def test_a_gc_can_always_edit_their_own_row(self):
        gc = self.mk("PEGCSELF", "GC", AccessLevel.GC)
        self.assertIn(gc.ir_id, self.editable(gc))

    # ── roles where view and edit already agreed, must keep agreeing ───────
    def test_an_admin_can_edit_everyone(self):
        admin = self.mk("PEADMIN", "Admin", AccessLevel.ADMIN)
        other = self.mk("PEOTHER", "Anyone", AccessLevel.IR)
        self.assertIn(other.ir_id, self.editable(admin))

    def test_a_ctc_can_edit_their_whole_subtree(self):
        ctc = self.mk("PECTC", "CTC", AccessLevel.CTC)
        deep = self.mk("PECTCDEEP", "Deep downline", AccessLevel.IR, parent=ctc)
        self.assertIn(deep.ir_id, self.editable(ctc))

    def test_an_ls_can_edit_their_team_members(self):
        ls = self.mk("PELS", "LS", AccessLevel.LS)
        member = self.mk("PELSMEM", "Team member", AccessLevel.IR)
        team = Team.objects.create(name="LS Team", created_by=ls)
        TeamMember.objects.create(team=team, ir=ls, role=TeamRole.LS)
        TeamMember.objects.create(team=team, ir=member, role=TeamRole.IR)
        self.assertIn(member.ir_id, self.editable(ls))

    def test_a_plain_ir_has_nobody_but_self(self):
        ir = self.mk("PEIR", "IR", AccessLevel.IR)
        self.assertEqual(self.editable(ir), {ir.ir_id})

    def test_the_editable_set_is_never_wider_than_the_viewable_set(self):
        """
        The whole point: EDIT must be a subset of VIEW for every role, so the
        picker (now built from EDIT) never offers someone view couldn't even
        justify showing in the first place.
        """
        ldc = self.mk("PESUBLDC", "LDC", AccessLevel.LDC)
        self.mk("PESUBDEEP", "Downline", AccessLevel.IR, parent=ldc)
        gc = self.mk("PESUBGC", "GC", AccessLevel.GC)
        self.mk("PESUBGCKID", "Referral", AccessLevel.IR, parent=gc)
        for who in (ldc, gc):
            self.assertTrue(self.editable(who) <= self.viewable(who),
                            f"{who.ir_id}'s editable set escaped its viewable set")


@override_settings(CACHES=LOCMEM)
class GetViewableIrsForPipelineEndpointTests(TestCase):
    """The picker's own HTTP endpoint now serves the editable set."""

    def mk(self, ir_id, name, lvl, parent=None):
        return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                 ir_password="x", ir_access_level=lvl, status=True,
                                 parent_ir=parent)

    def test_the_endpoint_excludes_someone_the_ldc_cannot_save_for(self):
        ldc = self.mk("EPLDC", "LDC", AccessLevel.LDC)
        deep = self.mk("EPDEEP", "Subtree only", AccessLevel.IR, parent=ldc)
        r = self.client.get(f"/api/pipeline_viewable_irs/{ldc.ir_id}/")
        self.assertEqual(r.status_code, 200)
        ids = {row["ir_id"] for row in r.json()}
        self.assertNotIn(deep.ir_id, ids)

    def test_editing_a_listed_person_actually_succeeds(self):
        """
        The end-to-end guarantee: whoever the endpoint offers, the save
        endpoint accepts — no more "shown, then refused" for this list.
        """
        ldc = self.mk("EPLDC2", "LDC", AccessLevel.LDC)
        member = self.mk("EPMEM2", "Team member", AccessLevel.IR, parent=ldc)
        team = Team.objects.create(name="T", created_by=ldc)
        TeamMember.objects.create(team=team, ir=ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=team, ir=member, role=TeamRole.IR)

        listed = {row["ir_id"] for row in
                  self.client.get(f"/api/pipeline_viewable_irs/{ldc.ir_id}/").json()}
        self.assertIn(member.ir_id, listed)

        r = self.client.put(f"/api/pipeline_stats/{member.ir_id}/update/",
                            data={"requester_ir_id": ldc.ir_id, "total_name_list": 5},
                            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content[:300])
