from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Sum
from django.test import TestCase, override_settings

from core.models import Ir, Team, TeamMember, TeamRole, UVDetail, AccessLevel
from core.utils.dates import get_week_info_friday_to_friday

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
YEAR = 2026


@override_settings(CACHES=LOCMEM)
class SetTeamUvTotalsTests(TestCase):
    """
    Reconciling a team's weekly UV totals to figures recorded outside the app.
    The arithmetic has to survive re-runs and must never quietly destroy UVs
    somebody actually entered.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)

        cls.head = mk("UHEAD", "Team Head", AccessLevel.LDC)
        cls.member = mk("UMEM", "A Member")
        cls.stranger = mk("USTRANGE", "Not In Team")

        cls.team = Team.objects.create(name="Champions United", created_by=cls.head)
        TeamMember.objects.create(team=cls.team, ir=cls.head, role=TeamRole.LDC)
        TeamMember.objects.create(team=cls.team, ir=cls.member, role=TeamRole.IR)

        # A second team whose UVs must never be counted.
        other = Team.objects.create(name="Other Team", created_by=cls.stranger)
        TeamMember.objects.create(team=other, ir=cls.stranger, role=TeamRole.IR)

    def mid_of(self, week):
        _, _, s, e = get_week_info_friday_to_friday(week_number=week, year=YEAR)
        return s + (e - s) / 2

    def uv(self, ir, week, count, comments=""):
        return UVDetail.objects.create(ir=ir, ir_name=ir.ir_name, uv_date=self.mid_of(week),
                                       uv_count=Decimal(str(count)), comments=comments)

    def week_total(self, week):
        _, _, s, e = get_week_info_friday_to_friday(week_number=week, year=YEAR)
        ids = list(TeamMember.objects.filter(team=self.team).values_list("ir_id", flat=True))
        return UVDetail.objects.filter(ir_id__in=ids, uv_date__gte=s, uv_date__lte=e
                                       ).aggregate(t=Sum("uv_count"))["t"] or Decimal("0")

    def run_cmd(self, totals, **kw):
        out = StringIO()
        call_command("set_team_uv_totals", team="Champions United", year=YEAR,
                     attribute_to=self.head.ir_id, totals=totals, stdout=out, **kw)
        return out.getvalue()

    # ── behaviour ────────────────────────────────────────────────────────
    def test_dry_run_writes_nothing(self):
        self.run_cmd("4:22.5")
        self.assertEqual(UVDetail.objects.count(), 0)

    def test_it_writes_the_supplied_total_into_an_empty_week(self):
        self.run_cmd("4:22.5", apply=True)
        self.assertEqual(self.week_total(4), Decimal("22.5"))

    def test_it_tops_up_only_the_difference(self):
        """A week that already has UVs must end at the figure, not above it."""
        self.uv(self.member, 4, 10)
        self.run_cmd("4:22.5", apply=True)
        self.assertEqual(self.week_total(4), Decimal("22.5"))
        # the member's own record is untouched
        self.assertEqual(UVDetail.objects.get(ir=self.member).uv_count, Decimal("10"))

    def test_a_week_that_is_already_correct_is_left_alone(self):
        self.uv(self.member, 5, 8)
        self.run_cmd("5:8", apply=True)
        self.assertEqual(UVDetail.objects.count(), 1, "no adjustment row should be added")
        self.assertEqual(self.week_total(5), Decimal("8"))

    def test_rerunning_does_not_double_the_correction(self):
        """The whole point of the marker on adjustment rows."""
        self.run_cmd("4:22.5", apply=True)
        self.run_cmd("4:22.5", apply=True)
        self.run_cmd("4:22.5", apply=True)
        self.assertEqual(self.week_total(4), Decimal("22.5"))

    def test_rerunning_with_a_changed_figure_corrects_rather_than_adds(self):
        self.run_cmd("4:22.5", apply=True)
        self.run_cmd("4:30", apply=True)
        self.assertEqual(self.week_total(4), Decimal("30"))

    def test_a_week_recorded_after_the_backfill_is_absorbed_on_rerun(self):
        """
        Someone enters a real UV later. Re-running must still land on the
        figure, reducing its own adjustment rather than stacking on top.
        """
        self.run_cmd("4:22.5", apply=True)
        self.uv(self.member, 4, 5)
        self.run_cmd("4:22.5", apply=True)
        self.assertEqual(self.week_total(4), Decimal("22.5"))

    def test_a_week_already_over_the_figure_is_skipped_by_default(self):
        self.uv(self.member, 7, 10)
        out = self.run_cmd("7:4", apply=True)
        self.assertEqual(self.week_total(7), Decimal("10"), "must not touch real UVs")
        self.assertIn("OVER", out)

    def test_zero_weeks_do_not_delete_what_people_recorded(self):
        self.uv(self.member, 6, 3)
        self.run_cmd("6:0", apply=True)
        self.assertEqual(self.week_total(6), Decimal("3"))

    def test_allow_reduce_brings_an_over_week_down(self):
        self.uv(self.member, 7, 10)
        self.run_cmd("7:4", apply=True, allow_reduce=True)
        self.assertEqual(self.week_total(7), Decimal("4"))
        self.assertEqual(UVDetail.objects.get(ir=self.member).uv_count, Decimal("10"),
                         "the original row survives; the correction is a separate row")

    def test_another_teams_uvs_are_never_counted(self):
        self.uv(self.stranger, 4, 100)
        self.run_cmd("4:22.5", apply=True)
        self.assertEqual(self.week_total(4), Decimal("22.5"))

    def test_uvs_land_inside_the_friday_to_friday_window(self):
        self.run_cmd("28:46", apply=True)
        _, _, s, e = get_week_info_friday_to_friday(week_number=28, year=YEAR)
        row = UVDetail.objects.get(comments__contains="[uv-backfill]")
        self.assertTrue(s <= row.uv_date <= e)
        self.assertEqual(self.week_total(28), Decimal("46"))
        self.assertEqual(self.week_total(27), Decimal("0"), "must not spill into the week before")
        self.assertEqual(self.week_total(29), Decimal("0"), "must not spill into the week after")

    # ── guards ───────────────────────────────────────────────────────────
    def test_it_refuses_an_ir_outside_the_team(self):
        with self.assertRaises(CommandError) as cm:
            call_command("set_team_uv_totals", team="Champions United", year=YEAR,
                         attribute_to=self.stranger.ir_id, totals="4:1", stdout=StringIO())
        self.assertIn("not a member", str(cm.exception))

    def test_it_refuses_an_ambiguous_team_name(self):
        Team.objects.create(name="Champions United B", created_by=self.head)
        with self.assertRaises(CommandError) as cm:
            self.run_cmd("4:1")
        self.assertIn("matches 2 teams", str(cm.exception))

    def test_it_refuses_an_unknown_team(self):
        with self.assertRaises(CommandError):
            call_command("set_team_uv_totals", team="Nope", year=YEAR,
                         attribute_to=self.head.ir_id, totals="4:1", stdout=StringIO())

    def test_it_parses_the_format_the_totals_were_given_in(self):
        out = self.run_cmd("Week 4 : 22.5\nWeek 5 : 8\nWeek 6 : 0")
        self.assertIn("22.5", out)
        self.assertIn("8", out)

    def test_it_rejects_a_negative_total(self):
        with self.assertRaises(CommandError):
            self.run_cmd("4:-5")
