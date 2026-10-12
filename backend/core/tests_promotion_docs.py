import json
from unittest import mock

from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir, PromotionDocument
from core.utils import private_storage

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
FIN, LEGAL, VIDEO = "financial_explanation", "legal_notary", "welcome_mandate_video"
PDF, MP4 = "application/pdf", "video/mp4"


@override_settings(CACHES=LOCMEM)
class PromotionDocsTests(TestCase):
    """
    A Player must send three things (financial explanation, legal notary,
    welcome mandate video) before anyone can promote them to IR. Files go to a
    private bucket; storage itself is replaced here.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, parent=None):
            ir = Ir(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_password="x",
                    ir_access_level=lvl, status=True, parent_ir=parent)
            ir.save()
            return ir
        cls.admin = mk("DADMIN", AccessLevel.ADMIN)
        cls.ldc = mk("DLDC", AccessLevel.LDC, cls.admin)
        cls.other_ldc = mk("DOLDC", AccessLevel.LDC, cls.admin)
        cls.player = mk("DPLAYER", AccessLevel.PLAYER, cls.ldc)
        cls.other_player = mk("DOTHER", AccessLevel.PLAYER, cls.ldc)

    def setUp(self):
        self.stored = {}  # key -> (size, content_type): what "arrived" in storage
        patches = [
            mock.patch.object(private_storage, "presign_upload", lambda key, ct, expires=900: f"https://r2.test/put/{key}"),
            mock.patch.object(private_storage, "presign_download", lambda key, name=None, expires=300: f"https://r2.test/get/{key}"),
            mock.patch.object(private_storage, "head", lambda key: self.stored.get(key)),
            mock.patch.object(private_storage, "delete", lambda key: self.deleted.append(key)),
            mock.patch.object(private_storage, "is_configured", lambda: True),
        ]
        self.deleted = []
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def post(self, url, body):
        return self.client.post(url, data=json.dumps(body), content_type="application/json")

    def send(self, kind, player="DPLAYER", by="DLDC", content_type=PDF, size=1000, name="doc.pdf"):
        """The whole flow, done by `by` (the promoter) for `player`: ask for a link, 'upload', confirm."""
        r = self.post(f"/api/promotion_docs/{player}/{kind}/upload_url/",
                      {"requester_ir_id": by, "filename": name, "content_type": content_type, "size": size})
        if r.status_code != 200:
            return r
        key = r.json()["key"]
        self.stored[key] = (size, content_type)
        return self.post(f"/api/promotion_docs/{player}/{kind}/confirm/",
                         {"requester_ir_id": by, "key": key, "filename": name})

    def send_all(self, player="DPLAYER", by="DLDC"):
        self.assertEqual(self.send(FIN, player, by).status_code, 201)
        self.assertEqual(self.send(LEGAL, player, by).status_code, 201)
        self.assertEqual(self.send(VIDEO, player, by, content_type=MP4, size=5_000_000, name="m.mp4").status_code, 201)

    def promote(self, actor, target):
        return self.post("/api/change_access_level/",
                         {"acting_ir_id": actor.ir_id, "target_ir_id": target.ir_id, "new_access_level": AccessLevel.IR})

    # --- the gate ---------------------------------------------------------

    def test_cannot_promote_until_all_three_are_sent(self):
        r = self.promote(self.admin, self.player)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(set(r.json()["missing_documents"]), {FIN, LEGAL, VIDEO})

        self.send(FIN)
        self.send(LEGAL)
        r = self.promote(self.admin, self.player)
        self.assertEqual(r.json()["missing_documents"], [VIDEO])

        self.send(VIDEO, content_type=MP4, size=5_000_000, name="m.mp4")
        self.assertEqual(self.promote(self.admin, self.player).status_code, 200)
        self.player.refresh_from_db()
        self.assertEqual(self.player.ir_access_level, AccessLevel.IR)

    def test_the_gate_applies_to_an_ldc_too(self):
        self.assertEqual(self.promote(self.ldc, self.player).status_code, 400)
        self.send_all()
        self.assertEqual(self.promote(self.ldc, self.player).status_code, 200)

    def test_the_other_promotion_route_is_gated_as_well(self):
        r = self.client.put(f"/api/update_ir/{self.player.ir_id}/",
                            data=json.dumps({"acting_ir_id": self.admin.ir_id, "ir_access_level": 6}),
                            content_type="application/json")
        self.assertEqual(r.status_code, 400, r.content)

    def test_demoting_to_player_needs_no_documents(self):
        ir = Ir.objects.create(ir_id="DIR", ir_name="x", ir_email="x@t.t", ir_password="x",
                               ir_access_level=AccessLevel.IR, status=True)
        r = self.post("/api/change_access_level/",
                      {"acting_ir_id": self.admin.ir_id, "target_ir_id": ir.ir_id, "new_access_level": AccessLevel.PLAYER})
        self.assertEqual(r.status_code, 200, r.content)

    # --- uploading --------------------------------------------------------

    def test_only_the_promoter_can_send_documents_for_a_player(self):
        self.assertEqual(self.send(FIN, by="DLDC").status_code, 201)       # their LDC
        self.assertEqual(self.send(FIN, by="DADMIN").status_code, 201)     # Admin
        self.assertEqual(self.send(FIN, by="DPLAYER").status_code, 403, "the Player cannot send their own")
        self.assertEqual(self.send(FIN, by="DOTHER").status_code, 403, "another Player")
        self.assertEqual(self.send(FIN, by="DOLDC").status_code, 403, "an LDC who does not promote this Player")
        self.assertEqual(self.send(FIN, player="DADMIN", by="DLDC").status_code, 400, "not a Player")

    def test_who_uploaded_is_recorded(self):
        self.send(FIN, by="DLDC")
        self.assertEqual(PromotionDocument.objects.get(ir=self.player, kind=FIN).uploaded_by_id, "DLDC")
        body = self.status("DLDC").json()
        self.assertEqual(next(d for d in body["documents"] if d["kind"] == FIN)["uploaded_by_name"], "DLDC")

    def test_file_type_and_size_are_enforced(self):
        self.assertEqual(self.send(FIN, content_type="application/zip").status_code, 400)
        self.assertEqual(self.send(FIN, size=50 * 1024 * 1024).status_code, 400)
        self.assertEqual(self.send(VIDEO, content_type=PDF).status_code, 400, "a PDF is not a video")
        self.assertEqual(self.send("passport").status_code, 404)

    def test_confirm_checks_the_file_really_arrived_and_is_theirs(self):
        key = self.post(f"/api/promotion_docs/DPLAYER/{FIN}/upload_url/",
                        {"requester_ir_id": "DLDC", "filename": "a.pdf", "content_type": PDF, "size": 10}).json()["key"]
        # Never uploaded:
        r = self.post(f"/api/promotion_docs/DPLAYER/{FIN}/confirm/", {"requester_ir_id": "DLDC", "key": key})
        self.assertEqual(r.status_code, 400)
        # Somebody else's key:
        self.stored["promotion/DOTHER__DOTHER/financial_explanation__ab12cd34__a.pdf"] = (10, PDF)
        r = self.post(f"/api/promotion_docs/DPLAYER/{FIN}/confirm/",
                      {"requester_ir_id": "DLDC", "key": "promotion/DOTHER__DOTHER/financial_explanation__ab12cd34__a.pdf"})
        self.assertEqual(r.status_code, 400)
        # Wrong thing actually uploaded under the right key:
        self.stored[key] = (10, "application/zip")
        self.assertEqual(self.post(f"/api/promotion_docs/DPLAYER/{FIN}/confirm/",
                                   {"requester_ir_id": "DLDC", "key": key}).status_code, 400)
        self.assertEqual(PromotionDocument.objects.count(), 0)
        self.assertIn(key, self.deleted, "a rejected upload is removed from storage")

    def test_sending_again_replaces_and_removes_the_old_file(self):
        self.send(FIN)
        first = PromotionDocument.objects.get(ir=self.player, kind=FIN).file_key
        self.send(FIN, name="v2.pdf")
        self.assertEqual(PromotionDocument.objects.filter(ir=self.player, kind=FIN).count(), 1)
        self.assertNotEqual(PromotionDocument.objects.get(ir=self.player, kind=FIN).file_key, first)
        self.assertIn(first, self.deleted)

    # --- viewing ----------------------------------------------------------

    def status(self, viewer, player="DPLAYER"):
        return self.client.get(f"/api/promotion_docs/{player}/?requester_ir_id={viewer}")

    def test_status_and_who_gets_a_view_link(self):
        self.send(FIN)
        for viewer in ("DLDC", "DADMIN"):
            body = self.status(viewer).json()
            fin = next(d for d in body["documents"] if d["kind"] == FIN)
            self.assertTrue(fin["view_url"].startswith("https://r2.test/get/promotion/DPLAYER__DPLAYER/"), viewer)

        # The Player sees what has been received, but not the papers themselves.
        body = self.status("DPLAYER").json()
        self.assertFalse(body["complete"])
        self.assertEqual(set(body["missing"]), {LEGAL, VIDEO})
        fin = next(d for d in body["documents"] if d["kind"] == FIN)
        self.assertTrue(fin["uploaded"])
        self.assertIsNone(fin["view_url"])

        self.assertEqual(self.status("DOLDC").status_code, 403)      # an LDC who does not promote them
        self.assertEqual(self.status("DOTHER").status_code, 403)     # another Player

    def test_promotable_list_shows_document_progress(self):
        self.send(FIN)
        rows = {p["ir_id"]: p for p in self.client.get("/api/promotable_players/?requester_ir_id=DLDC").json()["results"]}
        self.assertFalse(rows["DPLAYER"]["documents_complete"])
        self.assertEqual([d["uploaded"] for d in rows["DPLAYER"]["documents"]], [True, False, False])
        self.send_all("DOTHER", "DLDC")
        rows = {p["ir_id"]: p for p in self.client.get("/api/promotable_players/?requester_ir_id=DLDC").json()["results"]}
        self.assertTrue(rows["DOTHER"]["documents_complete"])

    def test_deleting_a_document_removes_its_file(self):
        self.send(FIN)
        doc = PromotionDocument.objects.get()
        key = doc.file_key
        doc.delete()
        self.assertIn(key, self.deleted)

    def test_storage_not_configured_is_a_clear_error(self):
        with mock.patch.object(private_storage, "presign_upload", side_effect=private_storage.StorageNotConfigured()):
            r = self.send(FIN)
        self.assertEqual(r.status_code, 503)

    def test_files_go_in_one_folder_named_after_the_player(self):
        Ir.objects.filter(pk="DPLAYER").update(ir_name="Ravi Kumar")
        self.send_all()
        keys = list(PromotionDocument.objects.filter(ir_id="DPLAYER").values_list("file_key", flat=True))
        self.assertEqual({k.split("/")[1] for k in keys}, {"Ravi-Kumar__DPLAYER"})
        self.assertEqual(len(keys), 3)

    def test_a_rename_does_not_split_the_folder(self):
        Ir.objects.filter(pk="DPLAYER").update(ir_name="Ravi Kumar")
        self.send(FIN)
        Ir.objects.filter(pk="DPLAYER").update(ir_name="Ravi K Sharma")
        self.send(LEGAL)
        folders = {k.split("/")[1] for k in PromotionDocument.objects.filter(ir_id="DPLAYER").values_list("file_key", flat=True)}
        self.assertEqual(folders, {"Ravi-Kumar__DPLAYER"})


class PrivateStorageKeyTests(TestCase):
    """The folder a Player's files live in, as it appears in the Cloudflare dashboard."""

    def test_folder_is_name_plus_id_and_path_safe(self):
        self.assertEqual(private_storage.person_folder("Ravi Kumar", "DU1234"), "Ravi-Kumar__DU1234")
        self.assertEqual(private_storage.person_folder("José  O'Neil / Jr.", "DU9"), "Jose-O-Neil-Jr__DU9")
        self.assertEqual(private_storage.person_folder("", "DU7"), "player__DU7")
        self.assertEqual(private_storage.person_folder("शिव", "DU8"), "player__DU8", "no ASCII letters to keep")
        self.assertNotIn("/", private_storage.person_folder("a/b/../c", "DU1"))

    def test_key_layout_and_ownership(self):
        key = private_storage.new_key("Ravi-Kumar__DU1234", "legal_notary", "my notary.pdf")
        self.assertRegex(key, r"^promotion/Ravi-Kumar__DU1234/legal_notary__[0-9a-f]{8}__my_notary\.pdf$")
        self.assertTrue(private_storage.key_belongs_to(key, "DU1234", "legal_notary"))
        self.assertFalse(private_storage.key_belongs_to(key, "DU1234", "financial_explanation"))
        self.assertFalse(private_storage.key_belongs_to(key, "U1234", "legal_notary"), "an ID that merely ends the same")
        self.assertFalse(private_storage.key_belongs_to(key, "DU12", "legal_notary"))
        self.assertFalse(private_storage.key_belongs_to("promotion/x/../../DU1234", "DU1234", "legal_notary"))

