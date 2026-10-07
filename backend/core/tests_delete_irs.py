import os
import tempfile
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.management.commands.delete_irs import parse_targets
from core.models import (
    AccessLevel, ChatMessage, ChatRoom, ChatRoomMember, ChatRoomType, InfoDetail,
    Ir, Notification, PlanDetail, UVDetail,
)

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

PASTED_TABLE = """#\tir_id\tir_name
1\tIM687020\tBhavith A T
2\tIM454366\tBhusarapu Lavanya
11\t   IM821457\tRakesh N
12\tIM015591\tRamyashree M G
"""


class ParseTests(TestCase):
    def test_pasted_table_parses_including_stray_whitespace_and_header(self):
        self.assertEqual(parse_targets(PASTED_TABLE), [
            ("IM687020", "Bhavith A T"),
            ("IM454366", "Bhusarapu Lavanya"),
            ("IM821457", "Rakesh N"),
            ("IM015591", "Ramyashree M G"),
        ])

    def test_plain_id_name_lines_and_lowercase_ids(self):
        self.assertEqual(parse_targets("im111414 Varsha G S\n"), [("IM111414", "Varsha G S")])

    def test_duplicate_id_is_an_error(self):
        from django.core.management.base import CommandError as CE
        with self.assertRaises(CE):
            parse_targets("IM111111 A\nIM111111 A\n")


@override_settings(CACHES=LOCMEM)
class DeleteIrsCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True, parent_ir=parent)

        cls.admin = mk("IM000001", "The Admin", AccessLevel.ADMIN)
        cls.ldc = mk("IM000002", "The LDC", AccessLevel.LDC, parent=cls.admin)
        cls.victim = mk("IM000003", "Victim One", parent=cls.ldc)
        cls.child = mk("IM000004", "Victims Child", parent=cls.victim)
        cls.grandchild = mk("IM000005", "Grandchild", parent=cls.child)
        cls.bystander = mk("IM000006", "Bystander", parent=cls.ldc)

        InfoDetail.objects.create(ir=cls.victim, info_date=timezone.now(), response="A", info_name="P")
        PlanDetail.objects.create(ir=cls.victim, plan_date=timezone.now(), plan_name="P")
        UVDetail.objects.create(ir=cls.victim, ir_name="Victim One", prospect_name="P", uv_count=1)
        room = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="G", created_by=cls.victim)
        ChatRoomMember.objects.create(room=room, ir=cls.victim)
        ChatRoomMember.objects.create(room=room, ir=cls.bystander)
        ChatMessage.objects.create(room=room, sender=cls.victim, content="hi")
        cls.room = room

    def run_cmd(self, lines, **opts):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("\n".join(lines) + "\n")
        out = StringIO()
        try:
            call_command("delete_irs", file=fh.name, stdout=out, **opts)
        finally:
            os.unlink(fh.name)
        return out.getvalue()

    def exists(self, ir):
        return Ir.objects.filter(ir_id=ir.ir_id).exists()

    # ── dry run ──────────────────────────────────────────────────────────
    def test_dry_run_deletes_nothing_and_reports_impact(self):
        out = self.run_cmd(["IM000003 Victim One"])
        self.assertTrue(self.exists(self.victim))
        self.assertEqual(InfoDetail.objects.filter(ir=self.victim).count(), 1)
        self.assertIn("REHEARSAL OK", out)
        self.assertIn("1 infos, 1 plans, 1 UV records", out)
        self.assertIn("1 chat messages", out)
        self.assertIn("INACTIVE", out)
        self.assertIn("IM000004", out)  # downline that gets re-attached

    # ── refusals ─────────────────────────────────────────────────────────
    def test_name_mismatch_refuses_even_with_apply(self):
        with self.assertRaises(CommandError):
            self.run_cmd(["IM000003 Somebody Else"], apply=True, confirm_count=1)
        self.assertTrue(self.exists(self.victim))

    def test_unknown_id_refuses(self):
        with self.assertRaises(CommandError):
            self.run_cmd(["IM999999 Nobody"], apply=True, confirm_count=1)

    def test_leaders_refused_without_flag(self):
        with self.assertRaises(CommandError):
            self.run_cmd(["IM000002 The LDC"], apply=True, confirm_count=1)
        self.assertTrue(self.exists(self.ldc))

    def test_apply_needs_the_right_confirm_count(self):
        with self.assertRaises(CommandError):
            self.run_cmd(["IM000003 Victim One"], apply=True)
        with self.assertRaises(CommandError):
            self.run_cmd(["IM000003 Victim One"], apply=True, confirm_count=5)
        self.assertTrue(self.exists(self.victim))

    def test_one_bad_line_blocks_the_whole_batch(self):
        with self.assertRaises(CommandError):
            self.run_cmd(["IM000003 Victim One", "IM000006 Wrong Name"], apply=True, confirm_count=2)
        self.assertTrue(self.exists(self.victim))
        self.assertTrue(self.exists(self.bystander))

    # ── apply ────────────────────────────────────────────────────────────
    def test_apply_deletes_cascades_and_reattaches_downlines(self):
        self.run_cmd(["IM000003 Victim One"], apply=True, confirm_count=1)
        self.assertFalse(self.exists(self.victim))
        self.assertEqual(InfoDetail.objects.filter(ir_id="IM000003").count(), 0)
        self.assertEqual(UVDetail.objects.filter(ir_id="IM000003").count(), 0)
        self.assertEqual(ChatMessage.objects.filter(room=self.room).count(), 0)

        self.child.refresh_from_db()
        self.assertEqual(self.child.parent_ir_id, "IM000002")
        self.assertEqual(self.child.hierarchy_path, "/IM000001/IM000002/IM000004/")
        self.grandchild.refresh_from_db()
        self.assertEqual(self.grandchild.hierarchy_path, self.child.hierarchy_path + "IM000005/")
        self.assertTrue(self.exists(self.bystander))

    def test_parent_and_child_both_on_the_list(self):
        """A stale copy of the child would still point at the deleted parent."""
        self.run_cmd(["IM000003 Victim One", "IM000004 Victims Child"], apply=True, confirm_count=2)
        self.assertFalse(self.exists(self.victim))
        self.assertFalse(self.exists(self.child))
        self.grandchild.refresh_from_db()
        self.assertEqual(self.grandchild.parent_ir_id, "IM000002")
        self.assertEqual(self.grandchild.hierarchy_path, "/IM000001/IM000002/IM000005/")

    def test_cascade_notifications_are_muted_but_ir_deleted_is_kept(self):
        before = set(Notification.objects.values_list("pk", flat=True))
        self.run_cmd(["IM000003 Victim One"], apply=True, confirm_count=1)
        types = set(Notification.objects.exclude(pk__in=before).values_list("notification_type", flat=True))
        self.assertNotIn(Notification.Type.UV_DELETED, types)
        self.assertNotIn(Notification.Type.PLAN_DELETED, types)
        self.assertNotIn(Notification.Type.MEMBER_DELETED, types)
        self.assertIn(Notification.Type.IR_DELETED, types)

    def test_quiet_suppresses_the_ir_deleted_notification_too(self):
        before = set(Notification.objects.values_list("pk", flat=True))
        self.run_cmd(["IM000003 Victim One"], apply=True, confirm_count=1, quiet=True)
        self.assertEqual(Notification.objects.exclude(pk__in=before).count(), 0)

    def test_notification_muting_does_not_leak_past_the_run(self):
        """A ContextVar, not a disconnect: nothing process-global to forget to restore."""
        from core import signals
        self.run_cmd(["IM000003 Victim One"], apply=True, confirm_count=1, quiet=True)
        self.assertEqual(signals._SUPPRESSED.get(), frozenset())

    def test_dry_run_shows_the_tree_changes(self):
        out = self.run_cmd(["IM000003 Victim One"])
        self.assertIn("IM000004 Victims Child", out)
        self.assertIn("IM000002 The LDC", out)


@override_settings(CACHES=LOCMEM)
class InactiveGroupTests(DeleteIrsCommandTests):
    """Groups owned by a deleted IR become read-only instead of ownerless."""

    def test_owned_group_is_marked_inactive_and_kept_with_its_history(self):
        self.run_cmd(["IM000003 Victim One"], apply=True, confirm_count=1)
        self.room.refresh_from_db()
        self.assertFalse(self.room.is_active)
        self.assertIsNone(self.room.created_by_id)
        # The surviving member is still in it.
        self.assertTrue(ChatRoomMember.objects.filter(room=self.room, ir=self.bystander).exists())

    def test_groups_owned_by_someone_not_deleted_are_untouched(self):
        other = ChatRoom.objects.create(room_type=ChatRoomType.GROUP, room_name="Keep", created_by=self.ldc)
        ChatRoomMember.objects.create(room=other, ir=self.victim)
        self.run_cmd(["IM000003 Victim One"], apply=True, confirm_count=1)
        other.refresh_from_db()
        self.assertTrue(other.is_active)

    def test_nobody_can_post_in_an_inactive_group(self):
        import json
        self.run_cmd(["IM000003 Victim One"], apply=True, confirm_count=1)
        r = self.client.post(
            f"/api/chat_rooms/{self.room.id}/messages/",
            data=json.dumps({"requester_ir_id": self.bystander.ir_id, "content": "hello?"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 403)
        self.assertIn("inactive", r.json()["detail"])

    def test_room_list_reports_is_active_false(self):
        self.run_cmd(["IM000003 Victim One"], apply=True, confirm_count=1)
        r = self.client.get("/api/chat_rooms/", {"requester_ir_id": self.bystander.ir_id})
        room = [x for x in r.json()["rooms"] if x["id"] == self.room.id][0]
        self.assertFalse(room["is_active"])

    def test_admin_handing_the_group_to_a_member_reactivates_it(self):
        import json
        self.run_cmd(["IM000003 Victim One"], apply=True, confirm_count=1)
        ChatRoomMember.objects.create(room=self.room, ir=self.admin)
        r = self.client.post(
            f"/api/chat_rooms/{self.room.id}/transfer_ownership/",
            data=json.dumps({"requester_ir_id": self.admin.ir_id, "new_owner_ir_id": self.bystander.ir_id}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.room.refresh_from_db()
        self.assertTrue(self.room.is_active)
        self.assertEqual(self.room.created_by_id, self.bystander.ir_id)
