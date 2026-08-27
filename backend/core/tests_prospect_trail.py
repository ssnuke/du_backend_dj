import datetime
from django.test import TestCase, override_settings
from django.utils import timezone
from core.models import (Ir, AccessLevel, InfoDetail, PlanDetail, InfoType,
                         Team, TeamMember, TeamRole)
from core.views.prospect import normalise_name


LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class ProspectTrailTests(TestCase):
    """
    One prospect's history across Info -> Re-info -> Plan, joined on a
    normalised name because nothing in the schema links the two tables.
    """

    @classmethod
    def setUpTestData(cls):
        def mk(ir_id, name, lvl=AccessLevel.IR, parent=None):
            return Ir.objects.create(ir_id=ir_id, ir_name=name, ir_email=f"{ir_id}@t.t",
                                     ir_password="x", ir_access_level=lvl, status=True,
                                     parent_ir=parent)

        cls.ldc = mk("TLDC", "The LDC", AccessLevel.LDC)
        cls.owner = mk("TOWNER", "Field IR", AccessLevel.IR, parent=cls.ldc)
        cls.outsider = mk("TOUT", "Somebody Else", AccessLevel.IR)

        team = Team.objects.create(name="T", created_by=cls.ldc)
        TeamMember.objects.create(team=team, ir=cls.ldc, role=TeamRole.LDC)
        TeamMember.objects.create(team=team, ir=cls.owner, role=TeamRole.IR)

        base = timezone.now() - datetime.timedelta(days=40)

        def info(name, days, kind=InfoType.FRESH, ir=None):
            InfoDetail.objects.create(ir=ir or cls.owner, info_name=name,
                                      info_date=base + datetime.timedelta(days=days),
                                      info_type=kind, response="A", comments="c")

        def plan(name, days, st="closing_pending", ir=None):
            return PlanDetail.objects.create(ir=ir or cls.owner, plan_name=name,
                                             plan_date=base + datetime.timedelta(days=days),
                                             status=st, plan_mode="virtual")

        # The trail we expect to reconstruct, with the name written three
        # slightly different ways — case and spacing are what normalisation
        # is for.
        info("Ramesh Kumar", 0)
        info("ramesh kumar", 5, InfoType.REINFO)
        info("  Ramesh   Kumar ", 9, InfoType.REINFO)
        plan("RAMESH KUMAR", 14, "closed")

        # A different prospect, and one belonging to someone out of scope.
        info("Sita Devi", 2)
        info("Hidden Person", 3, ir=cls.outsider)
        plan("Hidden Person", 6, ir=cls.outsider)

    def trail(self, viewer, q, **params):
        r = self.client.get(f"/api/prospect_trail/{viewer.ir_id}/", {"q": q, **params})
        self.assertEqual(r.status_code, 200, r.content[:200])
        return r.json()

    def test_name_normalisation_folds_case_and_spacing(self):
        self.assertEqual(normalise_name("  Ramesh   Kumar "), "ramesh kumar")
        self.assertEqual(normalise_name("RAMESH KUMAR"), "ramesh kumar")
        self.assertNotEqual(normalise_name("Ramesh K"), normalise_name("Ramesh Kumar"))

    def test_the_four_records_become_one_prospect(self):
        data = self.trail(self.ldc, "ramesh")
        self.assertEqual(len(data["prospects"]), 1)
        p = data["prospects"][0]
        self.assertEqual(p["counts"], {"info": 1, "reinfo": 2, "plan": 1})

    def test_events_are_in_chronological_order(self):
        p = self.trail(self.ldc, "ramesh")["prospects"][0]
        self.assertEqual([e["kind"] for e in p["events"]],
                         ["info", "reinfo", "reinfo", "plan"])
        dates = [e["date"] for e in p["events"]]
        self.assertEqual(dates, sorted(dates), "trail must read oldest to newest")

    def test_it_reports_where_the_prospect_ended_up(self):
        p = self.trail(self.ldc, "ramesh")["prospects"][0]
        self.assertEqual(p["latest_plan_status"], "closed")
        self.assertIsNotNone(p["first_seen"])
        self.assertIsNotNone(p["last_activity"])

    def test_a_prospect_with_no_plan_reports_no_outcome(self):
        p = self.trail(self.ldc, "sita")["prospects"][0]
        self.assertIsNone(p["latest_plan_status"])
        self.assertEqual(p["counts"]["plan"], 0)

    def test_records_outside_the_viewers_scope_are_not_returned(self):
        self.assertEqual(self.trail(self.ldc, "hidden")["prospects"], [])

    def test_the_owner_of_those_records_can_see_them(self):
        p = self.trail(self.outsider, "hidden")["prospects"][0]
        self.assertEqual(p["counts"], {"info": 1, "reinfo": 0, "plan": 1})

    def test_a_one_character_query_is_refused(self):
        self.assertEqual(self.trail(self.ldc, "r")["prospects"], [])

    def test_a_retired_fg_status_reads_as_in_process(self):
        """FG was removed as a status; the trail must not resurrect it."""
        PlanDetail.objects.create(ir=self.owner, plan_name="Ramesh Kumar",
                                  plan_date=timezone.now(), status="fg")
        p = self.trail(self.ldc, "ramesh")["prospects"][0]
        self.assertNotIn("fg", [e.get("status") for e in p["events"]])
        self.assertEqual(p["latest_plan_status"], "closed")

    def test_target_ir_id_narrows_to_one_persons_records(self):
        """
        The search box sits on whichever dashboard you are looking at, so on
        a colleague's dashboard it must answer about them, not about everyone
        the viewer happens to be able to see.
        """
        wide = self.trail(self.ldc, "ramesh")
        self.assertEqual(len(wide["prospects"]), 1)
        # Scoped to the LDC themselves, who recorded none of it.
        narrow = self.trail(self.ldc, "ramesh", target_ir_id=self.ldc.ir_id)
        self.assertEqual(narrow["prospects"], [])
        # Scoped to the IR who did record it.
        owned = self.trail(self.ldc, "ramesh", target_ir_id=self.owner.ir_id)
        self.assertEqual(len(owned["prospects"]), 1)

    def test_target_ir_id_cannot_be_used_to_reach_outside_scope(self):
        """Narrowing must intersect with the viewer's scope, never replace it."""
        r = self.trail(self.owner, "hidden", target_ir_id=self.outsider.ir_id)
        self.assertEqual(r["prospects"], [])
