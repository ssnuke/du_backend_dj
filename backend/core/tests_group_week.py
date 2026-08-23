import datetime
from django.test import TestCase
from core.models import (Ir, Team, TeamMember, Pocket, PocketMember,
                         InfoDetail, PlanDetail, WeeklyTarget, AccessLevel, TeamRole)
from core.views.get import build_group_week_block
from core.utils.dates import get_week_info_friday_to_friday, get_week_info_monday_to_sunday

WEEK, YEAR = 32, 2026

# Mirrors production week 32, team RELENTLESS RISERS.
SPEC = {
  "Snehith S Nair": (65, 6, [("Ronald Ryan G",23,0),("Prakhar Patni",17,1),
                             ("HEAD Snehith",3,2),("Shreyas MD",3,0)]),
  "Gagan's pocket": (60,10, [("Kiran Madivalar",32,1),("Hareesh SL",14,0),
                             ("HEAD Gagan",6,0),("Kailash",4,0),("Harshit",0,0)]),
  "Sunayana V Rao": (45, 6, [("HEAD Sunayana",7,0),("J JOY JOSHUA",1,0),
                             ("Anshu Gagana V R",1,0),("SHIVAKUMAR G",0,0)]),
}


class GroupWeekBlockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _, _, cls.ws, cls.we = get_week_info_friday_to_friday(week_number=WEEK, year=YEAR)
        _, _, cls.pws, cls.pwe = get_week_info_monday_to_sunday(week_number=WEEK, year=YEAR)
        cls.seq = 0

        def mk(name, lvl):
            cls.seq += 1
            return Ir.objects.create(ir_id=f"TST{cls.seq:05d}", ir_name=name,
                                     ir_email=f"t{cls.seq}@t.t", ir_password="x",
                                     ir_access_level=lvl, status=True)

        cls.team = Team.objects.create(name="RELENTLESS RISERS")
        cls.ls = mk("LS Leader", AccessLevel.LS)
        TeamMember.objects.create(team=cls.team, ir=cls.ls, role=TeamRole.LS)

        cls.gc_head = None
        for pname, (it, pt, members) in SPEC.items():
            pk = Pocket.objects.create(team=cls.team, name=pname, is_active=True)
            WeeklyTarget.objects.create(pocket=pk, week_number=WEEK, year=YEAR,
                week_start=cls.ws, week_end=cls.we,
                pocket_weekly_info_target=it, pocket_weekly_plan_target=pt)
            for nm, infos, plans in members:
                head = nm.startswith("HEAD")
                person = mk(nm.replace("HEAD ", ""), AccessLevel.GC if head else AccessLevel.IR)
                if head and pname == "Gagan's pocket":
                    cls.gc_head = person
                TeamMember.objects.create(team=cls.team, ir=person, role=TeamRole.IR)
                PocketMember.objects.create(pocket=pk, ir=person, role=TeamRole.IR, is_head=head)
                for k in range(infos):
                    InfoDetail.objects.create(ir=person, response="A", info_name=f"p{k}",
                        info_date=cls.ws + datetime.timedelta(days=k % 7, hours=2))
                for k in range(plans):
                    PlanDetail.objects.create(ir=person,
                        plan_date=cls.pws + datetime.timedelta(days=k % 6, hours=2))

        # One team member deliberately in no pocket.
        cls.loose = mk("Unpocketed Person", AccessLevel.IR)
        TeamMember.objects.create(team=cls.team, ir=cls.loose, role=TeamRole.IR)
        InfoDetail.objects.create(ir=cls.loose, response="A", info_name="x",
            info_date=cls.ws + datetime.timedelta(days=1, hours=2))

    def block(self, who):
        return build_group_week_block(who, WEEK, YEAR, self.ws, self.we, self.pws, self.pwe)

    def test_ls_sees_team_rolled_up_from_pockets(self):
        g = self.block(self.ls)
        self.assertEqual(g["kind"], "team")
        self.assertEqual(g["name"], "RELENTLESS RISERS")
        # 46 + 56 + 9 pocket infos, +1 from the unpocketed member
        self.assertEqual(g["totals"]["info_done"], 112)
        self.assertEqual(g["totals"]["plan_done"], 4)
        # team target unset -> summed from its pockets
        self.assertEqual(g["totals"]["info_target"], 170)
        self.assertEqual(g["totals"]["plan_target"], 22)
        self.assertEqual(g["target_source"], "rolled_up")

    def test_pockets_ranked_worst_first(self):
        g = self.block(self.ls)
        named = [(c["name"], c["info_done"], c["info_target"]) for c in g["children"]]
        self.assertEqual(named[0][0], "Sunayana V Rao")   # 9/45 = 20%
        self.assertEqual(named[1][0], "Snehith S Nair")   # 46/65 = 71%
        self.assertEqual(named[2][0], "Gagan's pocket")   # 56/60 = 93%

    def test_unpocketed_members_get_a_row(self):
        g = self.block(self.ls)
        loose = [c for c in g["children"] if c["kind"] == "unassigned"]
        self.assertEqual(len(loose), 1)
        # The LS is a team member who sits in no pocket, so they land here too
        # alongside the genuinely unassigned member. That is the point of the
        # row: without it the pocket rows quietly fail to add up.
        self.assertEqual(loose[0]["member_count"], 2)
        self.assertEqual(loose[0]["info_done"], 1)
        # untargeted rows sort last, not first
        self.assertEqual(g["children"][-1]["kind"], "unassigned")

    def test_children_account_for_the_whole_team(self):
        """The breakdown must reconcile with the headline, or the screen lies."""
        g = self.block(self.ls)
        for metric in ("info_done", "plan_done"):
            self.assertEqual(
                sum(c[metric] for c in g["children"]),
                g["totals"][metric],
                f"{metric}: pocket rows do not sum to the team total",
            )
        self.assertEqual(
            sum(c["member_count"] for c in g["children"]),
            g["member_count"],
        )

    def test_ls_sees_every_team_member_without_drilling_into_a_pocket(self):
        """The roster is the answer to "who do I push?", so it must be on the
        team view itself rather than a pocket screen away."""
        g = self.block(self.ls)
        names = [m["ir_name"] for m in g["members"]]
        self.assertEqual(len(names), g["member_count"])
        # ranked worst-last: top of the list is the busiest
        self.assertEqual(names[0], "Kiran Madivalar")
        self.assertIn("Harshit", names)
        # every row says which pocket to chase it through
        by_name = {m["ir_name"]: m for m in g["members"]}
        self.assertEqual(by_name["Kiran Madivalar"]["pocket_name"], "Gagan's pocket")
        self.assertEqual(by_name["Sunayana"]["pocket_name"], "Sunayana V Rao")
        self.assertTrue(by_name["Sunayana"]["is_head"])
        # someone in no pocket still appears, with no pocket to name
        self.assertIsNone(by_name["Unpocketed Person"]["pocket_name"])

    def test_gc_head_sees_own_pocket_members_ranked(self):
        g = self.block(self.gc_head)
        self.assertEqual(g["kind"], "pocket")
        self.assertEqual(g["name"], "Gagan's pocket")
        self.assertEqual(g["totals"]["info_done"], 56)
        self.assertEqual(g["totals"]["info_target"], 60)
        self.assertEqual(g["totals"]["plan_done"], 1)
        self.assertEqual([m["ir_name"] for m in g["members"]],
                         ["Kiran Madivalar", "Hareesh SL", "Gagan", "Kailash", "Harshit"])
        self.assertEqual(g["active_count"], 4)          # Harshit logged nothing
        self.assertEqual(g["member_count"], 5)
        self.assertEqual(g["children"], [])
        # a pocket has no pockets inside it, so no pocket tag on its rows
        self.assertTrue(all(m["pocket_name"] is None for m in g["members"]))

    def test_gc_sees_self_and_head_flags_and_day_buckets(self):
        g = self.block(self.gc_head)
        me = [m for m in g["members"] if m["is_self"]][0]
        self.assertEqual(me["ir_name"], "Gagan")
        self.assertTrue(me["is_head"])
        self.assertEqual(len(me["info_days"]), 6)       # 6 infos, one per day, days 0-5
        zero = [m for m in g["members"] if m["ir_name"] == "Harshit"][0]
        self.assertEqual(zero["info_days"], {})

    def test_gc_without_a_pocket_and_plain_ir_get_nothing(self):
        gc = Ir.objects.create(ir_id="TSTGC9", ir_name="GC No Pocket", ir_email="g@t.t",
                               ir_password="x", ir_access_level=AccessLevel.GC, status=True)
        ir = Ir.objects.create(ir_id="TSTIR9", ir_name="Plain IR", ir_email="i@t.t",
                               ir_password="x", ir_access_level=AccessLevel.IR, status=True)
        self.assertIsNone(self.block(gc))
        self.assertIsNone(self.block(ir))
