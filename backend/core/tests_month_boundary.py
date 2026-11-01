from django.test import TestCase

from core.utils.dates import (get_calendar_month_bounds, get_weeks_in_month,
                              get_week_info_friday_to_friday)


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
