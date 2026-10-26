import json
from unittest import mock

from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir, IrPendingRequest, PromotionDocument, Team, TeamMember, TeamRole
from core.utils import private_storage

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
FIN, LEGAL, VIDEO = "financial_explanation", "legal_notary", "welcome_mandate_video"
PDF, MP4 = "application/pdf", "video/mp4"


@override_settings(CACHES=LOCMEM)
class DocumentsForEveryoneTests(TestCase):
    """
    The three documents are kept for everyone except CTC and Admin. For an IR,
    PRO, LS or LDC only an LDC sends them (their team members, and their own). For a
    Player: the person, their LDC, or Admin/CTC. Admin/CTC/LDC can list what
    everyone sent.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, parent=None, name=None):
            ir = Ir(ir_id=ir_id, ir_name=name or ir_id, ir_email=f"{ir_id}@t.t", ir_password="x",
                    ir_access_level=lvl, status=True, parent_ir=parent)
            ir.save()
            return ir
        cls.admin = mk("EADMIN", AccessLevel.ADMIN)
        cls.ctc = mk("ECTC", AccessLevel.CTC, cls.admin)
        cls.ldc = mk("ELDC", AccessLevel.LDC, cls.ctc)
        cls.other_ldc = mk("EOLDC", AccessLevel.LDC, cls.ctc)
        cls.ls = mk("ELS", AccessLevel.LS, cls.ldc, name="Sita Rao")
        cls.pro = mk("EPRO", AccessLevel.GC, cls.ldc)
        cls.ir = mk("EIR", AccessLevel.IR, cls.ls, name="Ravi Kumar")
        cls.player = mk("EPLAYER", AccessLevel.PLAYER, cls.pro)
        cls.stranger = mk("ESTR", AccessLevel.IR, cls.other_ldc)
        # In the LDC's referral line but in none of their teams.
        cls.no_team = mk("ENOTEAM", AccessLevel.IR, cls.ls, name="Not In A Team")

        # Teams: an LDC's people for documents are the members of their teams.
        team = Team.objects.create(name="Team One", created_by=cls.ldc)
        for ir, role in ((cls.ldc, TeamRole.LDC), (cls.ls, TeamRole.LS), (cls.pro, TeamRole.GC), (cls.ir, TeamRole.IR)):
            TeamMember.objects.create(team=team, ir=ir, role=role)
        other_team = Team.objects.create(name="Team Two", created_by=cls.other_ldc)
        for ir, role in ((cls.other_ldc, TeamRole.LDC), (cls.stranger, TeamRole.IR)):
            TeamMember.objects.create(team=other_team, ir=ir, role=role)

    def setUp(self):
        self.stored = {}
        for p in [
            mock.patch.object(private_storage, "presign_upload", lambda key, ct, expires=900: f"https://r2.test/put/{key}"),
            mock.patch.object(private_storage, "presign_download", lambda key, name=None, expires=300: f"https://r2.test/get/{key}"),
            mock.patch.object(private_storage, "head", lambda key: self.stored.get(key)),
            mock.patch.object(private_storage, "delete", lambda key: None),
            mock.patch.object(private_storage, "is_configured", lambda: True),
        ]:
            p.start()
            self.addCleanup(p.stop)

    def post(self, url, body):
        return self.client.post(url, data=json.dumps(body), content_type="application/json")

    def send(self, kind, person, by, content_type=PDF, size=1000, name="doc.pdf"):
        r = self.post(f"/api/promotion_docs/{person}/{kind}/upload_url/",
                      {"requester_ir_id": by, "filename": name, "content_type": content_type, "size": size})
        if r.status_code != 200:
            return r
        key = r.json()["key"]
        self.stored[key] = (size, content_type)
        return self.post(f"/api/promotion_docs/{person}/{kind}/confirm/", {"requester_ir_id": by, "key": key, "filename": name})

    def status(self, person, viewer):
        return self.client.get(f"/api/promotion_docs/{person}/?requester_ir_id={viewer}")

    def overview(self, viewer, **params):
        q = "&".join(f"{k}={v}" for k, v in params.items())
        return self.client.get(f"/api/promotion_docs_overview/?requester_ir_id={viewer}&{q}")

    # ── who keeps documents ─────────────────────────────────────────────
    def test_the_people_who_can_send_their_own(self):
        """An LDC or a Player. An IR, PRO or LS cannot: their LDC validates and sends."""
        for who in ("ELDC", "EPLAYER"):
            self.assertEqual(self.send(FIN, who, who).status_code, 201, who)
        for who in ("EIR", "ELS", "EPRO"):
            self.assertEqual(self.send(FIN, who, who).status_code, 403, who)

    def test_admin_and_ctc_keep_no_documents(self):
        for who in ("EADMIN", "ECTC"):
            self.assertEqual(self.send(FIN, who, "EADMIN").status_code, 400, who)

    # ── PRO, LS and LDC: only an LDC sends ──────────────────────────────
    def test_an_ldc_sends_for_the_ir_pro_and_ls_in_their_teams(self):
        for person in ("ELS", "EPRO", "EIR"):
            self.assertEqual(self.send(FIN, person, "ELDC").status_code, 201, person)

    def test_a_downline_member_who_is_in_none_of_their_teams_is_not_theirs_to_send_for(self):
        r = self.send(FIN, "ENOTEAM", "ELDC")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.status("ENOTEAM", "ELDC").status_code, 403, "and cannot look at their files either")

    def test_an_ldc_cannot_send_for_another_ldc_or_another_branch(self):
        self.assertEqual(self.send(FIN, "EOLDC", "ELDC").status_code, 403, "another LDC")
        self.assertEqual(self.send(FIN, "ELDC", "EOLDC").status_code, 403, "and the other way round")
        other_ls = Ir.objects.create(ir_id="EOLS", ir_name="x", ir_email="x@t.t", ir_password="x",
                                     ir_access_level=AccessLevel.LS, status=True, parent_ir=self.other_ldc)
        self.assertEqual(self.send(FIN, other_ls.ir_id, "ELDC").status_code, 403, "an LS in another LDC's branch")

    def test_admin_and_ctc_cannot_send_for_an_ir_pro_ls_or_ldc(self):
        for by in ("EADMIN", "ECTC"):
            for person in ("EIR", "ELS", "EPRO", "ELDC"):
                r = self.send(FIN, person, by)
                self.assertEqual(r.status_code, 403, f"{by} -> {person}")
                self.assertIn("sent by an LDC", r.json()["detail"])

    def test_an_ir_pro_or_ls_cannot_send_for_anyone_else_either(self):
        self.assertEqual(self.send(FIN, "EIR", "ELS").status_code, 403)
        self.assertEqual(self.send(FIN, "ELS", "EPRO").status_code, 403)
        self.assertEqual(self.send(FIN, "ELS", "EIR").status_code, 403)

    # ── Player: unchanged ───────────────────────────────────────────────
    def test_admin_ctc_and_the_ldc_can_send_for_a_player(self):
        for by in ("EADMIN", "ECTC"):
            self.assertEqual(self.send(FIN, "EPLAYER", by).status_code, 201, by)
        self.assertEqual(self.send(FIN, "EPLAYER", "ELDC").status_code, 201, "their downline")

    def test_an_ldc_cannot_send_for_an_ir_in_another_ldcs_team(self):
        self.assertEqual(self.send(FIN, "ESTR", "ELDC").status_code, 403, "another LDC's team")
        self.assertEqual(self.send(FIN, "ESTR", "EOLDC").status_code, 201, "their own LDC can")

    def test_an_existing_ir_with_nothing_on_record_is_found_under_missing_and_can_be_sent_for(self):
        """Members who joined before documents were asked for: the LDC finds them and sends."""
        rows = self.overview("ELDC", show="pending").json()["results"]
        self.assertIn("EIR", {r["ir_id"] for r in rows})
        self.assertTrue(self.status("EIR", "ELDC").json()["can_upload"])
        self.assertEqual(self.send(FIN, "EIR", "ELDC").status_code, 201)

    def test_who_uploaded_is_recorded_for_both_routes(self):
        self.send(FIN, "EPLAYER", "EPLAYER")
        self.send(LEGAL, "EPLAYER", "ELDC")
        self.assertEqual(PromotionDocument.objects.get(ir_id="EPLAYER", kind=FIN).uploaded_by_id, "EPLAYER")
        self.assertEqual(PromotionDocument.objects.get(ir_id="EPLAYER", kind=LEGAL).uploaded_by_id, "ELDC")

    # ── the status tells the screen whether to offer upload ─────────────
    def test_status_says_who_gets_the_upload_buttons(self):
        def can(person, viewer):
            return self.status(person, viewer).json()["can_upload"]
        self.assertTrue(can("ELS", "ELDC"))
        self.assertFalse(can("ELS", "ELS"), "a LS sees their files but cannot send")
        self.assertFalse(can("ELS", "EADMIN"), "Admin can preview, not send")
        self.assertTrue(can("ELDC", "ELDC"))
        self.assertTrue(can("EIR", "ELDC"))
        self.assertFalse(can("EIR", "EIR"), "an IR sees their files but cannot send")
        self.assertFalse(can("EIR", "EADMIN"))
        self.assertTrue(can("EPLAYER", "EPLAYER"))
        self.assertTrue(can("EPLAYER", "EADMIN"))

    # ── who can open them ───────────────────────────────────────────────
    def test_own_files_and_leaders_can_open_but_not_peers(self):
        self.send(FIN, "EIR", "ELDC")
        for viewer in ("EIR", "ELDC", "EADMIN", "ECTC"):
            doc = next(d for d in self.status("EIR", viewer).json()["documents"] if d["kind"] == FIN)
            self.assertTrue(doc["view_url"], viewer)
        self.assertEqual(self.status("EIR", "ELS").status_code, 403)
        self.assertEqual(self.status("EIR", "ESTR").status_code, 403)
        self.assertEqual(self.status("EIR", "EOLDC").status_code, 403, "an LDC from another branch")

    # ── the overview (Manage Team) ──────────────────────────────────────
    def test_admin_sees_everyone_who_has_sent_something_newest_first(self):
        self.send(FIN, "ELS", "ELDC")
        self.send(FIN, "EIR", "ELDC")
        self.send(LEGAL, "EIR", "ELDC")
        body = self.overview("EADMIN").json()
        self.assertEqual([r["ir_id"] for r in body["results"]], ["EIR", "ELS"])
        ir_row = body["results"][0]
        self.assertEqual((ir_row["role"], ir_row["documents_count"], ir_row["complete"]), ("IR", 2, False))
        self.assertEqual({d["kind"]: d["uploaded"] for d in ir_row["documents"]}, {FIN: True, LEGAL: True, VIDEO: False})
        self.assertNotIn("view_url", json.dumps(body), "links come from the per-person call, not the list")

    def test_overview_never_lists_admin_ctc_or_the_viewer(self):
        self.send(FIN, "ELDC", "ELDC")
        self.send(FIN, "ELS", "ELDC")
        ids = {r["ir_id"] for r in self.overview("EADMIN", show="all").json()["results"]}
        self.assertFalse({"EADMIN", "ECTC"} & ids)
        ids = {r["ir_id"] for r in self.overview("ELDC", show="all").json()["results"]}
        self.assertNotIn("ELDC", ids)

    def test_an_ldc_sees_only_the_members_of_their_teams(self):
        self.send(FIN, "EIR", "ELDC")
        self.send(FIN, "ESTR", "EOLDC")
        self.send(FIN, "ENOTEAM", "EADMIN")          # an IR the LDC's team does not include
        self.assertEqual({r["ir_id"] for r in self.overview("ELDC").json()["results"]}, {"EIR"})
        self.assertEqual({r["ir_id"] for r in self.overview("EOLDC").json()["results"]}, {"ESTR"})
        everyone = {r["ir_id"] for r in self.overview("ECTC").json()["results"]}
        self.assertTrue({"EIR", "ESTR"} <= everyone)

    def test_missing_lists_only_team_members_not_the_whole_downline(self):
        ids = {r["ir_id"] for r in self.overview("ELDC", show="pending").json()["results"]}
        self.assertEqual(ids, {"ELS", "EPRO", "EIR"}, "ENOTEAM and the Player are in the referral line, not the team")
        self.assertIn("ENOTEAM", {r["ir_id"] for r in self.overview("EADMIN", show="pending").json()["results"]})

    def test_a_player_the_ldc_may_promote_stays_theirs_to_handle_though_not_in_this_list(self):
        """Reviewed in the Players tab; the promotion rule is unchanged."""
        ids = {r["ir_id"] for r in self.overview("ELDC", show="all").json()["results"]}
        self.assertNotIn("EPLAYER", ids)
        self.assertTrue(self.status("EPLAYER", "ELDC").json()["can_upload"])
        self.assertEqual(self.send(FIN, "EPLAYER", "ELDC").status_code, 201)

    def test_an_ldc_also_handles_a_player_they_registered_in_another_branch(self):
        IrPendingRequest.objects.create(
            action_type=IrPendingRequest.ActionType.REGISTER, requested_by=self.other_ldc,
            target_ir_code="EPLAYER", target_ir_name="P", request_status=IrPendingRequest.Status.APPROVED, payload={})
        self.assertEqual(self.send(FIN, "EPLAYER", "EOLDC").status_code, 201)

    def test_filters_search_and_paging(self):
        self.send(FIN, "EIR", "ELDC")
        for kind in (FIN, LEGAL):
            self.send(kind, "ELS", "ELDC")
        self.send(VIDEO, "ELS", "ELDC", content_type=MP4, size=5_000_000, name="m.mp4")
        pending = {r["ir_id"] for r in self.overview("EADMIN", show="pending").json()["results"]}
        self.assertIn("EIR", pending)
        self.assertNotIn("ELS", pending, "ELS sent all three")
        self.assertIn("ELDC", pending, "people who sent nothing are 'pending' too")
        by_name = self.overview("EADMIN", show="all", search="ravi").json()
        self.assertEqual([r["ir_id"] for r in by_name["results"]], ["EIR"])
        page = self.overview("EADMIN", show="all", limit=2).json()
        self.assertEqual((len(page["results"]), page["has_more"]), (2, True))
        self.assertEqual(self.overview("EADMIN", show="bogus").status_code, 400)

    def test_ls_ir_and_player_cannot_use_the_overview(self):
        for who in ("ELS", "EIR", "EPLAYER", "EPRO"):
            self.assertEqual(self.overview(who).status_code, 403, who)
