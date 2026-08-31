from datetime import timedelta

from django.test import TestCase

from core.utils.dates import (get_week_info_friday_to_friday,
                              get_week_info_monday_to_sunday)


class QnetSalesWeekTests(TestCase):
    """
    The app's week must be QNet's sales week: Friday 21:30 IST to the following
    Friday 21:29. QNet's own Sales Months calendar labels each week by its
    first FULL day (the Saturday) through its last (the Friday), which is what
    the expected dates below are taken from.
    """

    # Straight from the QNet Rank Dashboard "Sales Months" screens for 2026.
    QNET_2026 = {
        1: ("03 Jan", "09 Jan"), 2: ("10 Jan", "16 Jan"), 3: ("17 Jan", "23 Jan"),
        4: ("24 Jan", "30 Jan"), 5: ("31 Jan", "06 Feb"), 9: ("28 Feb", "06 Mar"),
        14: ("04 Apr", "10 Apr"), 17: ("25 Apr", "01 May"), 18: ("02 May", "08 May"),
        22: ("30 May", "05 Jun"), 26: ("27 Jun", "03 Jul"), 27: ("04 Jul", "10 Jul"),
        31: ("01 Aug", "07 Aug"), 35: ("29 Aug", "04 Sep"), 39: ("26 Sep", "02 Oct"),
        44: ("31 Oct", "06 Nov"), 48: ("28 Nov", "04 Dec"), 52: ("26 Dec", "01 Jan"),
    }

    def test_every_week_matches_the_qnet_calendar(self):
        for week, (start_label, end_label) in self.QNET_2026.items():
            _, _, start, end = get_week_info_friday_to_friday(week_number=week, year=2026)
            self.assertEqual((start + timedelta(days=1)).strftime("%d %b"), start_label,
                             f"week {week} start")
            self.assertEqual(end.strftime("%d %b"), end_label, f"week {week} end")

    def test_a_week_opens_friday_at_21_30(self):
        for week in (1, 17, 35, 52):
            _, _, start, _ = get_week_info_friday_to_friday(week_number=week, year=2026)
            self.assertEqual(start.strftime("%a %H:%M"), "Fri 21:30", f"week {week}")

    def test_a_week_closes_friday_at_21_29(self):
        for week in (1, 17, 35, 52):
            _, _, _, end = get_week_info_friday_to_friday(week_number=week, year=2026)
            self.assertEqual(end.strftime("%a %H:%M:%S"), "Fri 21:29:59", f"week {week}")

    def test_consecutive_weeks_never_overlap_or_leave_a_gap(self):
        """
        The week used to end Friday 23:30 while the next had already opened at
        21:30, so anything logged in those two hours was counted in BOTH weeks.
        """
        previous_end = None
        for year in (2025, 2026, 2027):
            for week in range(1, 53):
                _, _, start, end = get_week_info_friday_to_friday(week_number=week, year=year)
                if previous_end is not None:
                    gap = (start - previous_end).total_seconds()
                    self.assertGreaterEqual(gap, 0, f"{year} W{week} overlaps the week before")
                    self.assertLessEqual(gap, 1, f"{year} W{week} leaves a gap after the week before")
                previous_end = end

    def test_a_week_is_exactly_seven_days_long(self):
        for week in (1, 26, 52):
            _, _, start, end = get_week_info_friday_to_friday(week_number=week, year=2026)
            self.assertEqual((end - start) + timedelta(seconds=1), timedelta(days=7))

    def test_a_friday_evening_entry_belongs_to_the_week_that_just_opened(self):
        """The case the overlap got wrong."""
        _, _, w35_start, _ = get_week_info_friday_to_friday(week_number=35, year=2026)
        moment = w35_start + timedelta(minutes=30)          # Fri 28 Aug 22:00
        week_number, year, _, _ = get_week_info_friday_to_friday(moment)
        self.assertEqual((week_number, year), (35, 2026))

        _, _, _, w34_end = get_week_info_friday_to_friday(week_number=34, year=2026)
        self.assertLess(w34_end, moment, "week 34 must have closed before that moment")


class PlanWeekTests(TestCase):
    """Plans stay Monday to Sunday — a different cycle from the sales week."""

    def test_the_plan_week_runs_monday_to_sunday(self):
        _, _, start, end = get_week_info_monday_to_sunday(week_number=35, year=2026)
        self.assertEqual(start.strftime("%a %H:%M"), "Mon 00:00")
        self.assertEqual(end.strftime("%a"), "Sun")

    def test_plan_weeks_do_not_overlap(self):
        previous_end = None
        for week in range(1, 53):
            _, _, start, end = get_week_info_monday_to_sunday(week_number=week, year=2026)
            if previous_end is not None:
                self.assertGreaterEqual((start - previous_end).total_seconds(), 0,
                                        f"plan week {week} overlaps the one before")
            previous_end = end
