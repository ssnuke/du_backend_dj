import json
from unittest import mock

from django.test import TestCase, override_settings

from core.models import AccessLevel, Ir, InfoDetail
from core.utils import ir_deletion

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class AdminBulkDeleteIrsTests(TestCase):
    """
    POST /api/admin/delete_irs/ — the tree must come out exactly right.

        A (admin)
        └─ C (ctc)
           ├─ L1 (ldc)
           │  ├─ a1 ─┬─ b1 ── c1 ── f1
           │  │      └─ b2
           │  └─ a3
           └─ L2 (ldc)
              └─ d1 ── e1
    """

    @classmethod
    def setUpTestData(cls):
        def mk(i, name, lvl, parent=None):
            return Ir.objects.create(ir_id=i, ir_name=name, ir_email=f"{i}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True, parent_ir=parent)
        L = AccessLevel
        cls.A = mk("IM000001", "Admin", L.ADMIN)
        cls.C = mk("IM000002", "Ctc", L.CTC, cls.A)
        cls.L1 = mk("IM000003", "Ldc One", L.LDC, cls.C)
        cls.L2 = mk("IM000004", "Ldc Two", L.LDC, cls.C)
        cls.a1 = mk("IM000011", "A One", L.IR, cls.L1)
        cls.a3 = mk("IM000013", "A Three", L.IR, cls.L1)
        cls.b1 = mk("IM000021", "B One", L.IR, cls.a1)
        cls.b2 = mk("IM000022", "B Two", L.IR, cls.a1)
        cls.c1 = mk("IM000031", "C One", L.IR, cls.b1)
        cls.f1 = mk("IM000041", "F One", L.IR, cls.c1)
        cls.d1 = mk("IM000051", "D One", L.IR, cls.L2)
        cls.e1 = mk("IM000061", "E One", L.IR, cls.d1)
        InfoDetail.objects.create(ir=cls.a1, response="A", info_name="x")
        InfoDetail.objects.create(ir=cls.e1, response="A", info_name="y")  # a survivor's data

    def call(self, irs, requester=None, **extra):
        body = {"requester_ir_id": (requester or self.A).ir_id,
                "irs": [{"ir_id": i.ir_id, "ir_name": i.ir_name} for i in irs]}
        body.update(extra)
        return self.client.post("/api/admin/delete_irs/", data=json.dumps(body),
                                content_type="application/json")

    def ids(self):
        return set(Ir.objects.values_list("ir_id", flat=True))

    def assert_tree_consistent(self):
        self.assertEqual(ir_deletion.hierarchy_violations(), {})

    # ── auth / input ─────────────────────────────────────────────────────
    def test_non_admin_is_refused(self):
        r = self.call([self.a3], requester=self.L1)
        self.assertEqual(r.status_code, 403)
        self.assertIn(self.a3.ir_id, self.ids())

    def test_admin_cannot_delete_themselves_or_a_peer(self):
        r = self.call([self.A])
        self.assertEqual(r.status_code, 400)
        self.assertIn("problems", r.json())

    def test_leaders_need_the_explicit_flag(self):
        self.assertEqual(self.call([self.L2]).status_code, 400)
        self.assertIn(self.L2.ir_id, self.ids())

    def test_name_mismatch_blocks_the_whole_batch(self):
        body = {"requester_ir_id": self.A.ir_id, "apply": True, "confirm_count": 2,
                "irs": [{"ir_id": "IM000013", "ir_name": "A Three"}, {"ir_id": "IM000011", "ir_name": "Wrong"}]}
        r = self.client.post("/api/admin/delete_irs/", data=json.dumps(body), content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.ids(), set(Ir.objects.values_list("ir_id", flat=True)))
        self.assertIn("IM000013", self.ids())

    def test_pasted_text_table_is_accepted(self):
        body = {"requester_ir_id": self.A.ir_id,
                "text": "#\tir_id\tir_name\n1\tIM000013\tA Three\n2\t   IM000051\tD One\n"}
        r = self.client.post("/api/admin/delete_irs/", data=json.dumps(body), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["total"], 2)

    # ── rehearsal ────────────────────────────────────────────────────────
    def test_default_is_a_rehearsal_that_changes_nothing(self):
        before = {i.ir_id: (i.parent_ir_id, i.hierarchy_path, i.hierarchy_level) for i in Ir.objects.all()}
        r = self.call([self.a1, self.b1, self.d1])
        body = r.json()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(body["mode"], "dry_run")
        self.assertFalse(body["committed"])
        after = {i.ir_id: (i.parent_ir_id, i.hierarchy_path, i.hierarchy_level) for i in Ir.objects.all()}
        self.assertEqual(before, after)
        # ...but it tells you exactly what the tree would do.
        moved = {m["ir_id"]: m["new_parent_id"] for m in body["reparented"]}
        self.assertEqual(moved, {"IM000031": "IM000003", "IM000022": "IM000003", "IM000061": "IM000004"})

    def test_apply_requires_confirm_count(self):
        self.assertEqual(self.call([self.a3], apply=True).status_code, 400)
        self.assertEqual(self.call([self.a3], apply=True, confirm_count=9).status_code, 400)
        self.assertIn(self.a3.ir_id, self.ids())

    # ── the tree ─────────────────────────────────────────────────────────
    def test_parent_child_and_grandchild_on_the_list_collapse_correctly(self):
        r = self.call([self.a1, self.b1, self.d1], apply=True, confirm_count=3)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["committed"])
        self.assertEqual(self.ids() & {"IM000011", "IM000021", "IM000051"}, set())

        for ir_id, parent in [("IM000031", "IM000003"), ("IM000022", "IM000003"), ("IM000061", "IM000004")]:
            self.assertEqual(Ir.objects.get(ir_id=ir_id).parent_ir_id, parent, ir_id)
        # paths and levels of the survivors AND their own descendants were rewritten
        c1 = Ir.objects.get(ir_id="IM000031")
        self.assertEqual(c1.hierarchy_path, "/IM000001/IM000002/IM000003/IM000031/")
        self.assertEqual(c1.hierarchy_level, 3)
        f1 = Ir.objects.get(ir_id="IM000041")
        self.assertEqual(f1.hierarchy_path, "/IM000001/IM000002/IM000003/IM000031/IM000041/")
        self.assertEqual(Ir.objects.get(ir_id="IM000061").hierarchy_path, "/IM000001/IM000002/IM000004/IM000061/")
        self.assert_tree_consistent()

    def test_deleting_a_whole_chain_leaves_the_rest_untouched(self):
        self.call([self.a1, self.b1, self.c1], apply=True, confirm_count=3)
        self.assertEqual(Ir.objects.get(ir_id="IM000041").parent_ir_id, "IM000003")  # f1 → L1
        self.assertEqual(Ir.objects.get(ir_id="IM000022").parent_ir_id, "IM000003")  # b2 → L1
        self.assertEqual(Ir.objects.get(ir_id="IM000013").parent_ir_id, "IM000003")  # untouched sibling
        self.assert_tree_consistent()

    def test_survivors_keep_their_data(self):
        self.call([self.a1, self.d1], apply=True, confirm_count=2)
        self.assertEqual(InfoDetail.objects.filter(ir_id="IM000061").count(), 1)
        self.assertEqual(InfoDetail.objects.filter(ir_id="IM000011").count(), 0)

    def test_a_root_ir_with_children_leaves_them_as_roots(self):
        top = Ir.objects.create(ir_id="IM000070", ir_name="Top", ir_email="t@t.t", ir_password="x",
                                ir_access_level=AccessLevel.LDC, status=True)
        kid = Ir.objects.create(ir_id="IM000071", ir_name="Kid", ir_email="k@t.t", ir_password="x",
                                ir_access_level=AccessLevel.IR, status=True, parent_ir=top)
        self.call([top], apply=True, confirm_count=1, allow_leaders=True)
        kid.refresh_from_db()
        self.assertIsNone(kid.parent_ir_id)
        self.assertEqual(kid.hierarchy_path, "/IM000071/")
        self.assertEqual(kid.hierarchy_level, 0)

    def test_preexisting_tree_damage_elsewhere_is_reported_not_blamed(self):
        Ir.objects.filter(ir_id="IM000013").update(hierarchy_path="/wrong/")
        r = self.call([self.d1], apply=True, confirm_count=1)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertGreaterEqual(r.json()["checks"]["preexisting_hierarchy_violations"], 1)

    # ── the safety net ───────────────────────────────────────────────────
    def test_a_delete_that_breaks_the_tree_is_refused_and_nothing_is_deleted(self):
        real = ir_deletion._delete_all

        def broken(resolved, delete_ids):
            out = real(resolved, delete_ids)
            Ir.objects.filter(ir_id="IM000013").update(hierarchy_path="/corrupted/")  # simulated bug
            return out

        before = self.ids()
        with mock.patch.object(ir_deletion, "_delete_all", broken):
            r = self.call([self.a1, self.d1], apply=True, confirm_count=2)
        self.assertEqual(r.status_code, 409, r.content)
        self.assertFalse(r.json()["committed"])
        self.assertEqual(self.ids(), before)
        self.assertEqual(Ir.objects.get(ir_id="IM000013").hierarchy_path, "/IM000001/IM000002/IM000003/IM000013/")

    def test_a_delete_that_takes_someone_elses_data_is_refused(self):
        real = ir_deletion._delete_all

        def greedy(resolved, delete_ids):
            out = real(resolved, delete_ids)
            InfoDetail.objects.filter(ir_id="IM000061").delete()  # a survivor's info
            return out

        with mock.patch.object(ir_deletion, "_delete_all", greedy):
            r = self.call([self.a1], apply=True, confirm_count=1)
        self.assertEqual(r.status_code, 409)
        self.assertIn("IM000011", self.ids())
        self.assertEqual(InfoDetail.objects.filter(ir_id="IM000061").count(), 1)
