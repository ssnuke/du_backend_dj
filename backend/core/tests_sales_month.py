from datetime import date
from decimal import Decimal

from django.test import TestCase, override_settings

from core.models import (AccessLevel, InfoDetail, InfoResponse, Ir, PlanDetail, Team, TeamMember,
                         TeamRole, UVDetail)
from core.utils.dates import (get_current_sales_month, get_sales_month_for_week, get_sales_month_week_numbers,
                              get_sales_month_weeks, ist)

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class SalesMonthCalendarTests(TestCase):
    def test_matches_the_qnet_sales_months_screen(self):
        nov = get_sales_month_weeks(11, 2026)
        self.assertEqual([w["week_number"] for w in nov], [44, 45, 46, 47])
        self.assertEqual((nov[0]["sales_start"], nov[-1]["sales_end"]), (date(2026, 10, 31), date(2026, 11, 27)))
        dec = get_sales_month_weeks(12, 2026)
        self.assertEqual([w["week_number"] for w in dec], [48, 49, 50, 51, 52])
        self.assertEqual((dec[0]["sales_start"], dec[-1]["sales_end"]), (date(2026, 11, 28), date(2027, 1, 1)))
        # Week 43, the one above November on that screen, closes October.
        self.assertEqual(get_sales_month_weeks(10, 2026)[-1]["sales_end"], date(2026, 10, 30))

    def test_every_week_belongs_to_exactly_one_month(self):
        seen = []
        for m in range(1, 13):
            seen += get_sales_month_week_numbers(m)
        self.assertEqual(seen, list(range(1, 53)))
        for m in range(1, 13):
            for w in get_sales_month_week_numbers(m):
                self.assertEqual(get_sales_month_for_week(w), m)

    def test_months_run_back_to_back_with_no_gap_or_overlap(self):
        prev_end = None
        for m in range(1, 13):
            weeks = get_sales_month_weeks(m, 2026)
            if prev_end:
                self.assertEqual((weeks[0]["sales_start"] - prev_end).days, 1)
            prev_end = weeks[-1]["sales_end"]

    def test_current_sales_month_follows_the_sales_week_not_the_calendar(self):
        # Sunday 1 Nov 2026 is still calendar-Nov, but the Friday 30 Oct 21:30 start
        # of week 44 has passed; both say November here, so use a day where they differ:
        # Fri 30 Oct 22:00 is calendar October and already sales month 11.
        self.assertEqual(get_current_sales_month(ist(2026, 10, 30, 22, 0)), (11, 2026))
        self.assertEqual(get_current_sales_month(ist(2026, 10, 30, 20, 0)), (10, 2026))
        # Mon 30 Nov is calendar November and already sales month 12.
        self.assertEqual(get_current_sales_month(ist(2026, 11, 30, 10, 0)), (12, 2026))


@override_settings(CACHES=LOCMEM)
class SalesMonthSummaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ldc = Ir.objects.create(ir_id="SMLDC", ir_name="LDC", ir_email="sm@t.t", ir_password="x",
                                    ir_access_level=AccessLevel.LDC, status=True)
        cls.member = Ir.objects.create(ir_id="SMMEM", ir_name="Member", ir_email="sm2@t.t", ir_password="x",
                                       ir_access_level=AccessLevel.IR, status=True, parent_ir=cls.ldc)
        team = Team.objects.create(name="SM Team", created_by=cls.ldc)
        TeamMember.objects.create(team=team, ir=cls.ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=team, ir=cls.member, role=TeamRole.IR)

        def plan(name, when):
            PlanDetail.objects.create(ir=cls.member, plan_date=when, plan_name=name, status="closing_pending")

        # Plans are bucketed on Monday-Sunday weeks: Oct 26-Nov 1 is week 43, Nov 2-8 week 44,
        # Nov 30-Dec 6 week 48.
        plan("sun nov 1 (wk43)", ist(2026, 11, 1, 10))
        plan("mon nov 2 (wk44)", ist(2026, 11, 2, 10))
        plan("sun nov 29 (wk47)", ist(2026, 11, 29, 23, 0))
        plan("mon nov 30 (wk48)", ist(2026, 11, 30, 9))

        def info(when):
            InfoDetail.objects.create(ir=cls.member, info_date=when, info_name="P", response=InfoResponse.A)

        # Infos/UVs run Friday 21:30 to the next Friday 21:29.
        info(ist(2026, 10, 30, 20, 0))   # wk43 -> October
        info(ist(2026, 10, 30, 22, 0))   # wk44 -> November, though calendar October
        info(ist(2026, 11, 27, 20, 0))   # wk47 -> November
        info(ist(2026, 11, 27, 22, 0))   # wk48 -> December, though calendar November
        for when, n in ((ist(2026, 10, 30, 22, 0), "3"), (ist(2026, 11, 27, 22, 0), "5")):
            UVDetail.objects.create(ir=cls.member, ir_name="Member", uv_date=when, uv_count=Decimal(n))

    def summary(self, month, year=2026, **extra):
        r = self.client.get(f"/api/monthly_plan_summary/{self.ldc.ir_id}/", {"month": month, "year": year, **extra})
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()

    def test_the_month_lists_exactly_its_own_weeks_with_qnet_dates(self):
        nov = self.summary(11)
        self.assertEqual([w["week_number"] for w in nov["weeks"]], [44, 45, 46, 47])
        self.assertEqual((nov["sales_start"], nov["sales_end"]), ("2026-10-31", "2026-11-27"))
        self.assertEqual(nov["weeks"][0]["sales_start"], "2026-10-31")
        self.assertEqual(nov["weeks"][0]["sales_end"], "2026-11-06")
        self.assertEqual([w["week_number"] for w in self.summary(12)["weeks"]], [48, 49, 50, 51, 52])

    def test_plans_follow_their_week_not_the_calendar_month(self):
        nov, dec, octo = self.summary(11), self.summary(12), self.summary(10)
        self.assertEqual(nov["month_total"]["total"], 2, "Mon 2 Nov and Sun 29 Nov")
        self.assertEqual(dec["month_total"]["total"], 1, "Mon 30 Nov belongs to week 48")
        self.assertEqual(octo["month_total"]["total"], 1, "Sun 1 Nov is still plan week 43")
        self.assertEqual(nov["weekly"]["44"]["total"], 1)
        self.assertEqual(nov["weekly"]["47"]["total"], 1)

    def test_infos_and_uvs_follow_the_friday_window(self):
        nov, dec, octo = self.summary(11), self.summary(12), self.summary(10)
        self.assertEqual((octo["info_month_total"], nov["info_month_total"], dec["info_month_total"]), (1, 2, 1))
        self.assertEqual((octo["uv_month_total"], nov["uv_month_total"], dec["uv_month_total"]), (0, 3, 5))
        self.assertEqual(nov["info_weekly"]["44"], 1)
        self.assertEqual(nov["info_weekly"]["47"], 1)

    def test_nothing_is_counted_twice_or_dropped_across_the_months(self):
        months = [self.summary(m) for m in (10, 11, 12)]
        self.assertEqual(sum(m["month_total"]["total"] for m in months), 4)
        self.assertEqual(sum(m["info_month_total"] for m in months), 4)
        self.assertEqual(sum(m["uv_month_total"] for m in months), 8)

    def test_no_month_given_means_the_current_sales_month(self):
        r = self.client.get(f"/api/monthly_plan_summary/{self.ldc.ir_id}/")
        month, year = get_current_sales_month()
        self.assertEqual((r.json()["month"], r.json()["year"]), (month, year))

    def test_the_plan_list_for_a_month_matches_the_summary_total(self):
        for month in (10, 11, 12):
            plans = self.client.get(f"/api/team_plans/{self.ldc.ir_id}/", {"month": month, "year": 2026}).json()["plans"]
            self.assertEqual(len(plans), self.summary(month)["month_total"]["total"], month)
        names = {p["plan_name"] for p in
                 self.client.get(f"/api/team_plans/{self.ldc.ir_id}/", {"month": 12, "year": 2026}).json()["plans"]}
        self.assertEqual(names, {"mon nov 30 (wk48)"})

    def test_bad_month_is_rejected(self):
        r = self.client.get(f"/api/monthly_plan_summary/{self.ldc.ir_id}/", {"month": 13, "year": 2026})
        self.assertEqual(r.status_code, 400)
