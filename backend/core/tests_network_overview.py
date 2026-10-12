import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (AccessLevel, InfoDetail, Ir, IrPendingRequest,
                         IrStatusChange)

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class NetworkOverviewTests(TestCase):
    """Admin-only month-by-month view of registrations, deactivations and quiet IRs."""

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, lvl, parent=None, started_days_ago=0, status=True):
            ir = Ir(ir_id=ir_id, ir_name=ir_id, ir_email=f"{ir_id}@t.t", ir_password="x",
                    ir_access_level=lvl, status=status, parent_ir=parent)
            ir.save()
            Ir.objects.filter(pk=ir_id).update(started_date=timezone.now().date() - timedelta(days=started_days_ago))
            return Ir.objects.get(pk=ir_id)

        cls.admin = mk("NADMIN", AccessLevel.ADMIN, started_days_ago=400)
        cls.ldc = mk("NLDC", AccessLevel.LDC, cls.admin, started_days_ago=400)
        cls.busy = mk("NBUSY", AccessLevel.IR, cls.ldc, started_days_ago=200)
        cls.quiet = mk("NQUIET", AccessLevel.IR, cls.ldc, started_days_ago=200)
        cls.fresh = mk("NFRESH", AccessLevel.IR, cls.ldc, started_days_ago=5)   # too new to be "quiet"
        cls.player = mk("NPLAYER", AccessLevel.PLAYER, cls.ldc, started_days_ago=10)
        InfoDetail.objects.create(ir=cls.busy, response="A", info_name="x", info_date=timezone.now() - timedelta(days=3))

    def get(self, who, path="/api/admin/network_overview/", **params):
        q = "&".join(f"{k}={v}" for k, v in {"requester_ir_id": who.ir_id, **params}.items())
        return self.client.get(f"{path}?{q}")

    def test_admin_only(self):
        self.assertEqual(self.get(self.ldc).status_code, 403)
        self.assertEqual(self.get(self.admin).status_code, 200)

    def test_quiet_excludes_active_new_and_players(self):
        body = self.get(self.admin).json()
        self.assertEqual(body["now"]["quiet"], 3)  # admin, ldc and NQUIET — NOT busy, fresh or the player
        names = self.get(self.admin, "/api/admin/network_overview/names/",
                         kind="quiet", year=timezone.now().year, month=timezone.now().month).json()
        self.assertEqual({r["ir_id"] for r in names["results"]}, {"NADMIN", "NLDC", "NQUIET"})

    def test_registrations_counted_in_their_month_including_players(self):
        cur = self.get(self.admin).json()["months"][-1]
        # fresh (5 days ago) and the player (10 days ago) joined within the current month window only if
        # the calendar says so; assert via the drill-down to stay independent of the day of the month.
        names = self.get(self.admin, "/api/admin/network_overview/names/", kind="registered",
                         year=cur["year"], month=cur["month"]).json()
        self.assertEqual(names["total"], cur["registered"])

    def test_status_toggle_is_logged_once_per_real_change(self):
        def toggle(value):
            return self.client.patch(
                f"/api/ir/{self.busy.ir_id}/status/",
                data=json.dumps({"requester_ir_id": self.admin.ir_id, "status": value}),
                content_type="application/json")
        self.assertEqual(toggle(False).status_code, 200)
        self.assertEqual(toggle(False).status_code, 200)  # repeat: no new row
        self.assertEqual(IrStatusChange.objects.count(), 1)
        self.assertEqual(toggle(True).status_code, 200)
        self.assertEqual(IrStatusChange.objects.count(), 2)

        cur = self.get(self.admin).json()
        self.assertEqual(cur["months"][-1]["deactivated"], 1)
        self.assertEqual(cur["months"][-1]["reactivated"], 1)
        self.assertIsNotNone(cur["status_log_started"])

    def test_removed_counts_approved_deletions(self):
        IrPendingRequest.objects.create(
            action_type=IrPendingRequest.ActionType.DELETE, requested_by=self.ldc,
            target_ir_code="GONE", target_ir_name="Gone",
            request_status=IrPendingRequest.Status.APPROVED, reviewed_at=timezone.now(), payload={})
        self.assertEqual(self.get(self.admin).json()["months"][-1]["removed"], 1)

    def test_players_waiting(self):
        now = self.get(self.admin).json()["now"]
        self.assertEqual(now["players_waiting"], 1)
        self.assertEqual(now["oldest_player_wait_days"], 10)
