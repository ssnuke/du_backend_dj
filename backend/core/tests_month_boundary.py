from decimal import Decimal

from django.test import TestCase, override_settings

from core.models import (Ir, AccessLevel, PlanDetail, InfoDetail, UVDetail,
                         Team, TeamMember, TeamRole, InfoResponse)
from core.utils.dates import (get_calendar_month_bounds, get_weeks_in_month,
                              get_week_info_friday_to_friday,
                              get_week_info_monday_to_sunday)

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class CalendarMonthBoundsTests(TestCase):
    def test_bounds_are_midnight_to_midnight_ist(self):
        start, end = get_calendar_month_bounds(8, 2026)
        self.assertEqual(start.strftime("%Y-%m-%d %H:%M:%S"), "2026-08-01 00:00:00")
        self.assertEqual(end.strftime("%Y-%m-%d %H:%M:%S"), "2026-08-31 23:59:59")

    def test_handles_a_short_february(self):
        start, end = get_calendar_month_bounds(2, 2026)
        self.assertEqual(end.day, 28)

    def test_handles_a_leap_february(self):
        start, end = get_calendar_month_bounds(2, 2028)
        self.assertEqual(end.day, 29)


class WeeksInMonthOverlapTests(TestCase):
    """
    A week straddling two months has to appear in BOTH of them, or the days
    on one side have no bucket to land in. Confirmed empirically for full
    coverage in the tests below rather than asserted for one hand-picked case.
    """

    def test_a_boundary_week_appears_in_both_months_it_touches(self):
        # Plan week 35 (Mon 31 Aug - Sun 06 Sep 2026) straddles the boundary
        # the user reported: "Aug week ... ends at 4th September".
        aug_weeks = {w["week_number"] for w in get_weeks_in_month(8, 2026)}
        sep_weeks = {w["week_number"] for w in get_weeks_in_month(9, 2026)}
        self.assertIn(35, aug_weeks)
        self.assertIn(35, sep_weeks)

    def test_every_hour_of_every_month_is_covered_by_some_returned_week(self):
        """
        The general form of the bug: EVERY month boundary had this, not just
        Aug/Sep — the week before also donated August's first two days to
        July. Checked on both cycles, hourly, across three years.
        """
        from datetime import timedelta

        gaps = []
        for year in (2025, 2026, 2027):
            for month in range(1, 13):
                month_start, month_end = get_calendar_month_bounds(month, year)
                weeks = get_weeks_in_month(month, year)
                plan_windows = [(w["start"], w["end"]) for w in weeks]
                info_windows = [
                    get_week_info_friday_to_friday(week_number=w["week_number"], year=w["year"])[2:]
                    for w in weeks
                ]
                t = month_start
                while t <= month_end:
                    if not any(s <= t <= e for s, e in plan_windows):
                        gaps.append((year, month, "plan", t))
                    if not any(s <= t <= e for s, e in info_windows):
                        gaps.append((year, month, "info", t))
                    t += timedelta(hours=6)
        self.assertEqual(gaps, [], f"{len(gaps)} uncovered instant(s), e.g. {gaps[:3]}")


@override_settings(CACHES=LOCMEM)
class MonthlySummaryBoundaryTests(TestCase):
    """
    The actual complaint: days that fall in September were being counted
    under August because they belonged to a week that STARTED in August.
    """

    @classmethod
    def setUpTestData(cls):
        cls.ldc = Ir.objects.create(ir_id="MBLDC", ir_name="LDC", ir_email="mb@t.t",
                                    ir_password="x", ir_access_level=AccessLevel.LDC, status=True)
        cls.member = Ir.objects.create(ir_id="MBMEM", ir_name="Member", ir_email="mb2@t.t",
                                       ir_password="x", ir_access_level=AccessLevel.IR,
                                       status=True, parent_ir=cls.ldc)
        team = Team.objects.create(name="MB Team", created_by=cls.ldc)
        TeamMember.objects.create(team=team, ir=cls.ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=team, ir=cls.member, role=TeamRole.IR)

        # Plan week 35 (Mon 31 Aug - Sun 06 Sep 2026): one plan logged on the
        # last day of August, one logged in the first days of September.
        cls.aug31_plan_date = get_week_info_monday_to_sunday(week_number=35, year=2026)[2]  # Mon 31 Aug 00:00
        cls.sep02_plan_date = cls.aug31_plan_date.replace(day=2, month=9, hour=10)          # Wed 02 Sep

        PlanDetail.objects.create(ir=cls.member, plan_date=cls.aug31_plan_date,
                                  plan_name="Aug 31 plan", status="closing_pending")
        PlanDetail.objects.create(ir=cls.member, plan_date=cls.sep02_plan_date,
                                  plan_name="Sep 02 plan", status="closing_pending")

        # Info week 35 (Fri 28 Aug 21:30 - Fri 04 Sep 21:29): one just before
        # midnight on 31 Aug, one just after midnight on 1 Sep, one on 3 Sep.
        _, _, info_start, info_end = get_week_info_friday_to_friday(week_number=35, year=2026)
        cls.info_aug31_evening = info_start.replace(day=31, month=8, hour=23, minute=0)
        cls.info_sep01_morning = info_start.replace(day=1, month=9, hour=6, minute=0)
        cls.info_sep03 = info_start.replace(day=3, month=9, hour=12, minute=0)

        for when in (cls.info_aug31_evening, cls.info_sep01_morning, cls.info_sep03):
            InfoDetail.objects.create(ir=cls.member, info_date=when, info_name="P",
                                      response=InfoResponse.A)

        UVDetail.objects.create(ir=cls.member, ir_name="Member",
                                uv_date=cls.info_aug31_evening, uv_count=Decimal("3"))
        UVDetail.objects.create(ir=cls.member, ir_name="Member",
                                uv_date=cls.info_sep03, uv_count=Decimal("5"))

    def summary(self, month):
        r = self.client.get(f"/api/monthly_plan_summary/{self.ldc.ir_id}/",
                            {"month": month, "year": 2026})
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()

    def test_the_september_plan_counts_in_september_not_august(self):
        aug = self.summary(8)
        sep = self.summary(9)
        self.assertEqual(aug["month_total"]["total"], 1, "only the Aug 31 plan")
        self.assertEqual(sep["month_total"]["total"], 1, "only the Sep 02 plan")

    def test_the_boundary_week_shows_up_partially_in_both_months(self):
        aug = self.summary(8)
        sep = self.summary(9)
        self.assertEqual(aug["weekly"]["35"]["total"], 1)
        self.assertEqual(sep["weekly"]["35"]["total"], 1)

    def test_infos_split_by_the_actual_midnight_boundary(self):
        """
        This is finer than the week: the info CYCLE window doesn't even end
        at midnight (it ends Fri 21:29), so the 31 Aug 23:00 entry is inside
        week 35's info window on BOTH sides of the month split — it must
        land in August (calendar day), even though its info-week neighbour
        an hour into 1 Sep lands in September.
        """
        aug = self.summary(8)
        sep = self.summary(9)
        self.assertEqual(aug["info_month_total"], 1, "only the 31 Aug 23:00 entry")
        self.assertEqual(sep["info_month_total"], 2, "the two 1/3 Sep entries")

    def test_uvs_split_the_same_way(self):
        aug = self.summary(8)
        sep = self.summary(9)
        self.assertEqual(aug["uv_month_total"], 3)
        self.assertEqual(sep["uv_month_total"], 5)

    def test_nothing_is_double_counted_across_the_two_months(self):
        aug = self.summary(8)
        sep = self.summary(9)
        self.assertEqual(aug["month_total"]["total"] + sep["month_total"]["total"], 2)
        self.assertEqual(aug["info_month_total"] + sep["info_month_total"], 3)
        self.assertEqual(aug["uv_month_total"] + sep["uv_month_total"], 8)


@override_settings(CACHES=LOCMEM)
class AggregatedPlansMonthBoundaryTests(TestCase):
    """GetTeamAggregatedPlans' month-mode drill-down list has the same fix."""

    @classmethod
    def setUpTestData(cls):
        cls.ldc = Ir.objects.create(ir_id="ABLDC", ir_name="LDC", ir_email="ab@t.t",
                                    ir_password="x", ir_access_level=AccessLevel.LDC, status=True)
        team = Team.objects.create(name="AB Team", created_by=cls.ldc)
        TeamMember.objects.create(team=team, ir=cls.ldc, role=TeamRole.LDC)

        aug31 = get_week_info_monday_to_sunday(week_number=35, year=2026)[2]
        sep02 = aug31.replace(day=2, month=9, hour=10)
        PlanDetail.objects.create(ir=cls.ldc, plan_date=aug31, plan_name="Aug plan",
                                  status="closing_pending")
        PlanDetail.objects.create(ir=cls.ldc, plan_date=sep02, plan_name="Sep plan",
                                  status="closing_pending")

    def test_month_mode_only_returns_that_calendar_months_plans(self):
        r = self.client.get(f"/api/team_plans/{self.ldc.ir_id}/", {"month": 8, "year": 2026})
        self.assertEqual(r.status_code, 200, r.content[:300])
        names = {p["plan_name"] for p in r.json()["plans"]}
        self.assertEqual(names, {"Aug plan"})

        r = self.client.get(f"/api/team_plans/{self.ldc.ir_id}/", {"month": 9, "year": 2026})
        names = {p["plan_name"] for p in r.json()["plans"]}
        self.assertEqual(names, {"Sep plan"})
