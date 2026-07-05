from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from core.models import (
    AccessLevel,
    Ir,
    InfoDetail,
    PlanDetail,
    Team,
    TeamMember,
    TeamRole,
    WeeklyTarget,
)


class GetTeamMembersQueryCountTests(TestCase):
    """
    Guards against the GetTeamMembers N+1 regression: listing team members
    must not issue more queries as the member count grows (was 4 extra
    queries per member — InfoDetail/PlanDetail/UVDetail/WeeklyTarget).
    """

    def setUp(self):
        self.owner = Ir.objects.create(
            ir_id="GTMOWN1", ir_name="Owner", ir_access_level=AccessLevel.LDC, status=True
        )
        self.team = Team.objects.create(name="Team", created_by=self.owner)

    def _add_members(self, count, prefix):
        for i in range(count):
            ir = Ir.objects.create(
                ir_id=f"{prefix}{i}", ir_name=f"Member{i}", ir_access_level=AccessLevel.IR, status=True
            )
            TeamMember.objects.create(team=self.team, ir=ir, role=TeamRole.IR)
            InfoDetail.objects.create(ir=ir, response="A", info_name="test")
            PlanDetail.objects.create(ir=ir, plan_name="plan", plan_date=timezone.now())

    def _get_members(self):
        return self.client.get(f"/api/team_members/{self.team.id}/")

    def test_query_count_flat_as_members_grow(self):
        self._add_members(3, "GTMA")
        with CaptureQueriesContext(connection) as ctx_small:
            resp = self._get_members()
        self.assertEqual(resp.status_code, 200)
        small_count = len(ctx_small.captured_queries)

        self._add_members(15, "GTMB")
        with CaptureQueriesContext(connection) as ctx_large:
            resp = self._get_members()
        self.assertEqual(resp.status_code, 200)
        large_count = len(ctx_large.captured_queries)

        # Before the fix this scaled ~4 queries/member. After the fix it
        # should stay roughly constant regardless of member count.
        self.assertLess(large_count, small_count + 4)
        self.assertEqual(len(resp.json()), 18)


class GetTargetsDashboardQueryCountTests(TestCase):
    """
    Guards against the GetTargetsDashboard N+1 regression: the per-team
    progress block must not issue more queries as the number of visible
    teams grows (was ~5 extra queries per team).
    """

    def setUp(self):
        self.ldc = Ir.objects.create(
            ir_id="GTDLDC1", ir_name="LDC", ir_access_level=AccessLevel.LDC, status=True
        )

    def _add_team_with_members(self, name, member_count, prefix):
        team = Team.objects.create(name=name, created_by=self.ldc)
        TeamMember.objects.create(team=team, ir=self.ldc, role=TeamRole.LDC)
        for i in range(member_count):
            ir = Ir.objects.create(
                ir_id=f"{prefix}{i}", ir_name=f"M{i}", ir_access_level=AccessLevel.IR, status=True
            )
            TeamMember.objects.create(team=team, ir=ir, role=TeamRole.IR)
            InfoDetail.objects.create(ir=ir, response="A", info_name="test")
        return team

    def _get_dashboard(self):
        return self.client.get(f"/api/targets_dashboard/{self.ldc.ir_id}/")

    def test_query_count_flat_as_teams_grow(self):
        self._add_team_with_members("Team A", 3, "GTDA")
        with CaptureQueriesContext(connection) as ctx_small:
            resp = self._get_dashboard()
        self.assertEqual(resp.status_code, 200)
        small_count = len(ctx_small.captured_queries)

        for n in range(4):
            self._add_team_with_members(f"Team {n}", 3, f"GTDX{n}_")

        with CaptureQueriesContext(connection) as ctx_large:
            resp = self._get_dashboard()
        self.assertEqual(resp.status_code, 200)
        large_count = len(ctx_large.captured_queries)

        # Before the fix this scaled ~5 queries/team. After the fix it
        # should stay roughly constant regardless of team count.
        self.assertLess(large_count, small_count + 5)
        self.assertEqual(len(resp.json()["teams"]), 5)


class GetHierarchyTreeQueryCountTests(TestCase):
    """
    Guards against the GetHierarchyTree recursive N+1 regression: building
    the tree must not issue more queries as the subtree grows (was up to 2
    queries per node).
    """

    def setUp(self):
        self.root = Ir.objects.create(
            ir_id="GHTROOT", ir_name="Root", ir_access_level=AccessLevel.ADMIN, status=True
        )

    def _build_chain(self, depth, prefix, parent):
        current = parent
        for i in range(depth):
            current = Ir.objects.create(
                ir_id=f"{prefix}{i}", ir_name=f"N{i}", ir_access_level=AccessLevel.IR,
                status=True, parent_ir=current,
            )
        return current

    def _get_tree(self):
        return self.client.get(f"/api/hierarchy_tree/{self.root.ir_id}/")

    def test_query_count_flat_as_subtree_grows(self):
        self._build_chain(3, "GHTA", self.root)
        with CaptureQueriesContext(connection) as ctx_small:
            resp = self._get_tree()
        self.assertEqual(resp.status_code, 200)
        small_count = len(ctx_small.captured_queries)

        for n in range(5):
            self._build_chain(3, f"GHTB{n}_", self.root)

        with CaptureQueriesContext(connection) as ctx_large:
            resp = self._get_tree()
        self.assertEqual(resp.status_code, 200)
        large_count = len(ctx_large.captured_queries)

        # Before the fix this scaled ~2 queries/node. After the fix it's a
        # single subtree query regardless of node count.
        self.assertEqual(small_count, large_count)
        self.assertEqual(resp.json()["total_downlines"], 3 + 5 * 3)
