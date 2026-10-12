"""
Link existing records into Info -> Plan -> UV trails.

Before the links existed, a prospect's info, plan and UV were three unrelated
rows tied together only by the name someone typed each time. This pairs them up
retroactively, by IR plus normalised name:

  * a UV is linked to the plan with the same name, shown on or before the UV;
  * a plan is linked to the info with the same name, logged on or before it.

Only unambiguous matches are written. If a name matches several candidates
(the same person re-planned, a re-info) the row is left unlinked and reported,
because guessing wrong would attach UVs to the wrong plan — worse than leaving
it for a person to link.

Dry run by default. Linking uses queryset .update(), which bypasses
post_save, so no "UV Record Updated" notification goes out for old history.
"""
import re
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import InfoDetail, PlanDetail, UVDetail


def normalise(name):
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


class Command(BaseCommand):
    help = "Link existing UVs to their plans and plans to their infos (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the links. Without it, only report.")

    def handle(self, *args, **opts):
        apply = opts["apply"]
        with transaction.atomic():
            plan_links, plan_skipped = self._link_uvs_to_plans(apply)
            info_links, info_skipped = self._link_plans_to_infos(apply)
            if not apply:
                transaction.set_rollback(True)

        mode = "Linked" if apply else "Would link"
        self.stdout.write(f"{mode} {plan_links} UV(s) to plans ({plan_skipped} left unlinked: no match or ambiguous).")
        self.stdout.write(f"{mode} {info_links} plan(s) to infos ({info_skipped} left unlinked: no match or ambiguous).")
        if not apply:
            self.stdout.write("Dry run — nothing written. Re-run with --apply.")

    def _link_uvs_to_plans(self, apply):
        plans_by_key = defaultdict(list)
        for p in PlanDetail.objects.exclude(status="rejected").only("id", "ir_id", "plan_name", "plan_date"):
            if p.plan_name:
                plans_by_key[(p.ir_id, normalise(p.plan_name))].append(p)

        linked = skipped = 0
        for uv in UVDetail.objects.filter(plan__isnull=True).exclude(prospect_name=""):
            candidates = [
                p for p in plans_by_key.get((uv.ir_id, normalise(uv.prospect_name)), [])
                if p.plan_date <= uv.uv_date
            ]
            if len(candidates) != 1:
                skipped += 1
                continue
            linked += 1
            if apply:
                UVDetail.objects.filter(pk=uv.pk).update(plan=candidates[0])
        return linked, skipped

    def _link_plans_to_infos(self, apply):
        infos_by_key = defaultdict(list)
        for i in InfoDetail.objects.only("id", "ir_id", "info_name", "info_date"):
            infos_by_key[(i.ir_id, normalise(i.info_name))].append(i)

        linked = skipped = 0
        for plan in PlanDetail.objects.filter(info__isnull=True).exclude(plan_name__isnull=True).exclude(plan_name=""):
            candidates = [
                i for i in infos_by_key.get((plan.ir_id, normalise(plan.plan_name)), [])
                if i.info_date <= plan.plan_date
            ]
            if len(candidates) != 1:
                skipped += 1
                continue
            linked += 1
            if apply:
                PlanDetail.objects.filter(pk=plan.pk).update(info=candidates[0])
        return linked, skipped
