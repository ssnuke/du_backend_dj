from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Sum
from django.test import TestCase, override_settings

from core.models import Ir, Team, TeamMember, TeamRole, UVDetail, AccessLevel, Notification
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
        self.assertIn("in none of the teams", str(cm.exception))

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


@override_settings(CACHES=LOCMEM)
class AttributeToLdcTests(TestCase):
    """--attribute-to defaults to the team's own LDC, so nobody has to look up
    an ir_id and risk pinning a year of corrections on the wrong person."""

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)
        cls.ldc = mk("ALDC", "The LDC", AccessLevel.LDC)
        cls.member = mk("AMEM", "A Member")
        cls.team = Team.objects.create(name="Champions United", created_by=cls.ldc)
        TeamMember.objects.create(team=cls.team, ir=cls.ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=cls.team, ir=cls.member, role=TeamRole.IR)

    def test_it_finds_the_ldc_without_being_told(self):
        out = StringIO()
        call_command("set_team_uv_totals", team="Champions United", year=YEAR,
                     totals="4:22.5", apply=True, stdout=out)
        row = UVDetail.objects.get(comments__contains="[uv-backfill]")
        self.assertEqual(row.ir_id, self.ldc.ir_id)
        self.assertIn("The LDC", out.getvalue())

    def test_an_explicit_ir_id_still_wins(self):
        call_command("set_team_uv_totals", team="Champions United", year=YEAR,
                     attribute_to=self.member.ir_id, totals="4:22.5", apply=True,
                     stdout=StringIO())
        self.assertEqual(UVDetail.objects.get(comments__contains="[uv-backfill]").ir_id,
                         self.member.ir_id)

    def test_two_ldcs_on_one_team_is_refused_rather_than_guessed(self):
        second = Ir.objects.create(ir_id="ALDC2", ir_name="Other LDC", ir_email="a2@t.t",
                                   ir_password="x", ir_access_level=AccessLevel.LDC, status=True)
        TeamMember.objects.create(team=self.team, ir=second, role=TeamRole.LDC)
        with self.assertRaises(CommandError) as cm:
            call_command("set_team_uv_totals", team="Champions United", year=YEAR,
                         totals="4:1", stdout=StringIO())
        self.assertIn("has 2 LDCs", str(cm.exception))


class ParseBlocksTests(TestCase):
    """
    The figures arrive pasted, in four different shapes, several teams at a
    time. Parsing them as sent is the point: retyping them into some other
    format is where a 42 becomes a 4.2.
    """

    def setUp(self):
        from core.management.commands.set_team_uv_totals import Command
        self.parse = Command()._parse_blocks

    def test_it_reads_every_format_the_figures_arrive_in(self):
        blocks = self.parse(
            "Champions United:\nWeek 1 : 0\nWeek 4 : 22.5\n"
            "\nDREAMERS:\nWeek - 1 : 6\nWeek - 11 : 70\n"
            "\nNumber 1:\nWeek 4: 12.5\n"
            "\nKINGDOM BUILDERS\nWeek 5 - 8.5\n"
        )
        self.assertEqual(set(blocks), {"Champions United", "DREAMERS", "Number 1", "KINGDOM BUILDERS"})
        self.assertEqual(blocks["Champions United"], {1: Decimal("0"), 4: Decimal("22.5")})
        self.assertEqual(blocks["DREAMERS"], {1: Decimal("6"), 11: Decimal("70")})
        self.assertEqual(blocks["Number 1"], {4: Decimal("12.5")})
        self.assertEqual(blocks["KINGDOM BUILDERS"], {5: Decimal("8.5")})

    def test_a_team_name_that_looks_numeric_is_not_mistaken_for_a_week(self):
        """\"Number 1\" and \"Track 1\" are team names, not week lines."""
        blocks = self.parse("Number 1:\nWeek 1: 3\n\nTrack 1:\nWeek 1 : 15.5\n")
        self.assertEqual(set(blocks), {"Number 1", "Track 1"})
        self.assertEqual(blocks["Track 1"], {1: Decimal("15.5")})

    def test_weeks_before_any_team_name_are_refused(self):
        with self.assertRaises(CommandError) as cm:
            self.parse("Week 1 : 5\n")
        self.assertIn("before any team", str(cm.exception))

    def test_a_team_with_no_weeks_is_refused(self):
        with self.assertRaises(CommandError) as cm:
            self.parse("Champions United:\n\nDREAMERS:\nWeek 1 : 6\n")
        self.assertIn("Champions United", str(cm.exception))

    def test_a_week_given_twice_with_different_totals_is_refused(self):
        with self.assertRaises(CommandError) as cm:
            self.parse("A:\nWeek 3 : 5\nWeek 3 : 9\n")
        self.assertIn("twice", str(cm.exception))

    def test_a_single_team_list_still_works_with_an_explicit_name(self):
        blocks = self.parse("1:0\n4:22.5", default_team="Champions United")
        self.assertEqual(blocks, {"Champions United": {1: Decimal("0"), 4: Decimal("22.5")}})

    def test_the_real_pasted_file_parses_to_the_figures_given(self):
        import os
        path = ("/private/tmp/claude-501/-Users-snehith-Work-Development-DU/"
                "afa83da4-57d2-4795-a7d2-bc26e9bd0acd/scratchpad/uv_backfill.txt")
        if not os.path.isfile(path):
            self.skipTest("data file not present")
        blocks = self.parse(path)
        self.assertEqual(len(blocks), 6)
        self.assertEqual(len(blocks["DREAMERS"]), 35, "DREAMERS runs to week 35")
        self.assertEqual(len(blocks["Track 1"]), 33, "Track 1 stops at week 33")
        for name in ("Champions United", "Number 1", "Goal Diggers", "KINGDOM BUILDERS"):
            self.assertEqual(len(blocks[name]), 34, name)
        self.assertEqual(blocks["DREAMERS"][11], Decimal("70"))
        self.assertEqual(blocks["Track 1"][13], Decimal("42"))
        self.assertEqual(blocks["Champions United"][28], Decimal("46"))


@override_settings(CACHES=LOCMEM)
class TeamGroupTests(TestCase):
    """
    Some figure sets cover an LDC's whole group rather than one team, so the
    weekly figure has to be matched against the combined total.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)
        cls.ldc = mk("GLDC", "Group LDC", AccessLevel.LDC)
        cls.a1 = mk("GA1", "In team A")
        cls.b1 = mk("GB1", "In team B")
        cls.both = mk("GBOTH", "In both teams")

        cls.ta = Team.objects.create(name="Alpha", created_by=cls.ldc)
        cls.tb = Team.objects.create(name="Beta", created_by=cls.ldc)
        for t in (cls.ta, cls.tb):
            TeamMember.objects.create(team=t, ir=cls.ldc, role=TeamRole.LDC)
            TeamMember.objects.create(team=t, ir=cls.both, role=TeamRole.IR)
        TeamMember.objects.create(team=cls.ta, ir=cls.a1, role=TeamRole.IR)
        TeamMember.objects.create(team=cls.tb, ir=cls.b1, role=TeamRole.IR)

    def mid(self, w):
        _, _, s, e = get_week_info_friday_to_friday(week_number=w, year=YEAR)
        return s + (e - s) / 2

    def group_total(self, week):
        _, _, s, e = get_week_info_friday_to_friday(week_number=week, year=YEAR)
        ids = set(TeamMember.objects.filter(team__in=[self.ta, self.tb]).values_list("ir_id", flat=True))
        return UVDetail.objects.filter(ir_id__in=ids, uv_date__gte=s, uv_date__lte=e
                                       ).aggregate(t=Sum("uv_count"))["t"] or Decimal("0")

    def run_group(self, totals, **kw):
        out = StringIO()
        call_command("set_team_uv_totals", year=YEAR, team="THE GROUP",
                     teams="Alpha,Beta", totals=totals, stdout=out, **kw)
        return out.getvalue()

    def test_the_figure_is_matched_across_both_teams(self):
        UVDetail.objects.create(ir=self.a1, ir_name="x", uv_date=self.mid(4), uv_count=Decimal("2"))
        UVDetail.objects.create(ir=self.b1, ir_name="x", uv_date=self.mid(4), uv_count=Decimal("3"))
        self.run_group("4:10", apply=True)
        self.assertEqual(self.group_total(4), Decimal("10"), "5 existing + 5 adjustment")

    def test_a_shared_member_is_counted_once_per_team_like_the_app_does(self):
        """
        The app totals a group by summing each team separately, so somebody in
        two of them counts twice. Deduplicating instead produced totals that
        agreed with themselves but not with the number on screen — a Track 1
        week read 22 in the app against a supplied figure of 13.
        """
        UVDetail.objects.create(ir=self.both, ir_name="x", uv_date=self.mid(4), uv_count=Decimal("4"))
        out = self.run_group("4:10")
        # 4 counted in Alpha AND Beta = 8, so the top-up is 2, not 6.
        self.assertIn("add 2", out)

    def test_the_shared_ldc_is_found_across_the_group(self):
        out = self.run_group("4:10", apply=True)
        self.assertIn("Group LDC", out)
        self.assertEqual(UVDetail.objects.get(comments__contains="[uv-backfill]").ir_id, self.ldc.ir_id)

    def test_two_different_ldcs_across_the_group_is_refused(self):
        other = Ir.objects.create(ir_id="GLDC2", ir_name="Other LDC", ir_email="g2@t.t",
                                  ir_password="x", ir_access_level=AccessLevel.LDC, status=True)
        TeamMember.objects.create(team=self.tb, ir=other, role=TeamRole.LDC)
        with self.assertRaises(CommandError) as cm:
            self.run_group("4:10")
        self.assertIn("has 2 LDCs", str(cm.exception))

    def test_an_actor_in_only_one_of_the_teams_is_accepted(self):
        """Raasitha leads two of her three teams but not the third."""
        call_command("set_team_uv_totals", year=YEAR, team="THE GROUP", teams="Alpha,Beta",
                     attribute_to=self.a1.ir_id, totals="4:10", apply=True, stdout=StringIO())
        self.assertEqual(UVDetail.objects.get(comments__contains="[uv-backfill]").ir_id, self.a1.ir_id)

    def test_teams_with_more_than_one_block_is_refused(self):
        with self.assertRaises(CommandError) as cm:
            self.run_group("A:\nWeek 1 : 1\n\nB:\nWeek 1 : 2\n")
        self.assertIn("exactly one block", str(cm.exception))


@override_settings(CACHES=LOCMEM)
class BackfillIsSilentTests(TestCase):
    """
    core/signals.py notifies uplines on every UVDetail save and delete. Right
    for somebody entering a UV today; wrong for reconciling a year of history,
    which sent 186 "New UV Record Added" alerts to real phones for UVs dated
    months earlier before this was caught.
    """

    @classmethod
    def setUpTestData(cls):
        cls.ldc = Ir.objects.create(ir_id="SLDC1", ir_name="Silent LDC", ir_email="s@t.t",
                                    ir_password="x", ir_access_level=AccessLevel.LDC, status=True)
        cls.mem = Ir.objects.create(ir_id="SMEM1", ir_name="Member", ir_email="s2@t.t",
                                    ir_password="x", ir_access_level=AccessLevel.IR,
                                    status=True, parent_ir=cls.ldc)
        cls.team = Team.objects.create(name="Silent Team", created_by=cls.ldc)
        TeamMember.objects.create(team=cls.team, ir=cls.ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=cls.team, ir=cls.mem, role=TeamRole.IR)

    def test_backfilling_sends_no_notifications(self):
        before = Notification.objects.count()
        call_command("set_team_uv_totals", team="Silent Team", year=YEAR,
                     totals="4:22.5,5:8,6:3", apply=True, stdout=StringIO())
        self.assertEqual(UVDetail.objects.filter(comments__contains="[uv-backfill]").count(), 3)
        self.assertEqual(Notification.objects.count(), before,
                         "a history backfill must not notify anyone")

    def test_rerunning_deletes_its_old_rows_silently_too(self):
        """The delete path notifies as well, so re-running must stay silent."""
        call_command("set_team_uv_totals", team="Silent Team", year=YEAR,
                     totals="4:22.5", apply=True, stdout=StringIO())
        before = Notification.objects.count()
        call_command("set_team_uv_totals", team="Silent Team", year=YEAR,
                     totals="4:30", apply=True, stdout=StringIO())
        self.assertEqual(Notification.objects.count(), before)

    def test_normal_uv_entry_still_notifies(self):
        """Muting must be scoped to the backfill, not left switched off."""
        before = Notification.objects.count()
        UVDetail.objects.create(ir=self.mem, ir_name=self.mem.ir_name,
                                prospect_name="A real prospect", uv_count=2)
        self.assertGreater(Notification.objects.count(), before,
                           "ordinary UV entry must still notify uplines")


@override_settings(CACHES=LOCMEM)
class DistributeTests(TestCase):
    """
    Spreading a week's adjustment across a group's teams, so it is counted
    once rather than multiplied by however many of the teams the person
    happens to belong to.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True)
        cls.ldc = mk("DLDC", "Everywhere LDC", AccessLevel.LDC)
        cls.teams = []
        for i in range(4):
            t = Team.objects.create(name=f"Team{i}", created_by=cls.ldc)
            TeamMember.objects.create(team=t, ir=cls.ldc, role=TeamRole.LDC)   # in ALL four
            TeamMember.objects.create(team=t, ir=mk(f"DS{i}", f"Solo {i}"), role=TeamRole.IR)
            cls.teams.append(t)

    def app_total(self, week):
        _, _, s, e = get_week_info_friday_to_friday(week_number=week, year=YEAR)
        tot = Decimal("0")
        for t in self.teams:
            ids = TeamMember.objects.filter(team=t).values_list("ir_id", flat=True)
            tot += UVDetail.objects.filter(ir_id__in=ids, uv_date__gte=s, uv_date__lte=e
                                           ).aggregate(x=Sum("uv_count"))["x"] or Decimal("0")
        return tot

    def run_group(self, totals, **kw):
        out = StringIO()
        call_command("set_team_uv_totals", year=YEAR, team="GRP",
                     teams=",".join(t.name for t in self.teams),
                     totals=totals, distribute=True, stdout=out, **kw)
        return out.getvalue()

    def test_the_group_total_is_exact_as_the_app_computes_it(self):
        self.run_group("4:22.5", apply=True)
        self.assertEqual(self.app_total(4), Decimal("22.5"))

    def test_a_figure_that_does_not_divide_evenly_still_lands_exactly(self):
        """7.5 over 4 teams is 1.875 each — the remainder must not be lost."""
        self.run_group("5:7.5", apply=True)
        self.assertEqual(self.app_total(5), Decimal("7.5"))

    def test_no_share_lands_on_someone_in_more_than_one_team(self):
        self.run_group("4:22.5", apply=True)
        for row in UVDetail.objects.filter(comments__contains="[uv-backfill]"):
            n = TeamMember.objects.filter(team__in=self.teams, ir_id=row.ir_id).count()
            self.assertEqual(n, 1, f"{row.ir_id} is in {n} teams; its row would be counted {n}x")

    def test_rerunning_still_lands_exactly(self):
        self.run_group("4:22.5", apply=True)
        self.run_group("4:22.5", apply=True)
        self.assertEqual(self.app_total(4), Decimal("22.5"))

    def test_the_split_helper_always_sums_back_in_half_steps(self):
        """
        Every part must be a real UV figure. Dividing 10 over 3 teams gave
        3.33/3.33/3.34 before — thirds of a UV, which do not exist.
        """
        from core.management.commands.set_team_uv_totals import Command
        for amt in ["7.5", "22.5", "6.5", "-6.5", "13", "-149.5", "1.5", "10", "0"]:
            for n in (2, 3, 4, 5):
                parts = Command._split(Decimal(amt), n)
                self.assertEqual(sum(parts), Decimal(amt), f"{amt} over {n}")
                self.assertEqual(len(parts), n)
                for p in parts:
                    self.assertEqual(p % Decimal("0.5"), 0, f"{p} is not a whole half-UV")

    def test_ten_over_three_teams_is_halves_not_thirds(self):
        from core.management.commands.set_team_uv_totals import Command
        self.assertEqual(sorted(Command._split(Decimal("10"), 3)),
                         [Decimal("3.0"), Decimal("3.5"), Decimal("3.5")])

    def test_an_amount_that_is_not_a_half_multiple_is_refused(self):
        from core.management.commands.set_team_uv_totals import Command
        with self.assertRaises(CommandError):
            Command._split(Decimal("0.25"), 2)
