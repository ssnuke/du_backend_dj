"""
Reconcile a team's weekly UV totals to a supplied set of figures.

Written because UV history for past weeks was recorded outside the app and has
to be brought in line week by week. It is a reconciliation, not an import: for
each week it works out what the team currently totals and writes only the
difference, so a week that is already correct is left completely alone.
"""
import os
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Sum

from core.models import Ir, Team, TeamMember, UVDetail
from core.utils.dates import get_week_info_friday_to_friday

# Every row this command creates carries this marker in its comments. It is
# what makes a re-run adjust the row it wrote last time instead of stacking a
# second one on top — without it, running twice would double the correction.
MARKER = "[uv-backfill]"


class Command(BaseCommand):
    help = (
        "Set a team's total UVs for given weeks to supplied figures, by writing a "
        "single adjustment row per week for the difference between the supplied "
        "total and what the team already has.\n\n"
        "UVs are bucketed on the INFO week (Friday 21:30 -> the following Friday), "
        "which is the window every dashboard in the app uses for UV totals — not "
        "the Monday-to-Sunday plan week. A UV logged on a Friday evening therefore "
        "belongs to the week that just opened.\n\n"
        "Re-runnable: adjustment rows are tagged, so running again corrects the row "
        "it wrote before rather than adding another.\n\n"
        "Defaults to a dry run. Pass --apply to write. Weeks where the team already "
        "has MORE than the supplied figure are reported and skipped unless "
        "--allow-reduce is given, because the only way to bring a total down is to "
        "alter UVs somebody actually recorded."
    )

    def add_arguments(self, parser):
        parser.add_argument("--team", required=True,
                            help='Team name, e.g. "Champions United". Must match exactly one team.')
        parser.add_argument("--year", type=int, required=True, help="Year the week numbers belong to.")
        parser.add_argument("--attribute-to", required=True,
                            help="ir_id that adjustment rows are recorded against. UVs belong to a "
                                 "person, not a team, so this IR's own weekly numbers will include "
                                 "the adjustment. Must be a member of the team.")
        parser.add_argument("--totals", required=True,
                            help='Week totals as "week:total" pairs, comma separated, '
                                 'e.g. "1:0,2:0,4:22.5". Or a path to a file with one pair per line.')
        parser.add_argument("--apply", action="store_true",
                            help="Actually write. Without this, only reports what would change.")
        parser.add_argument("--allow-reduce", action="store_true",
                            help="Permit reducing a week that is already above its supplied figure. "
                                 "This edits or deletes rows other people entered.")

    # ── inputs ────────────────────────────────────────────────────────────
    def _parse_totals(self, raw):
        # Only treat the argument as a path when it plausibly is one: an inline
        # list always contains a colon, and a stray open() on arbitrary input
        # throws for non-path types rather than falling through.
        text = raw
        if isinstance(raw, str) and ":" not in raw and os.path.isfile(raw):
            with open(raw) as fh:
                text = fh.read()

        totals = {}
        for chunk in text.replace("\n", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            # tolerates "Week 4 : 22.5" as well as "4:22.5"
            cleaned = chunk.lower().replace("week", "").strip()
            if ":" not in cleaned:
                raise CommandError(f"Cannot read week total from {chunk!r} — expected 'week:total'")
            wk, val = cleaned.split(":", 1)
            try:
                week = int(wk.strip())
                total = Decimal(val.strip())
            except (ValueError, ArithmeticError):
                raise CommandError(f"Cannot read week total from {chunk!r}")
            if week in totals and totals[week] != total:
                raise CommandError(f"Week {week} given twice with different totals")
            if total < 0:
                raise CommandError(f"Week {week} has a negative total")
            totals[week] = total
        if not totals:
            raise CommandError("No week totals supplied")
        return dict(sorted(totals.items()))

    def _resolve_team(self, name):
        matches = list(Team.objects.filter(name__icontains=name.strip()))
        if not matches:
            raise CommandError(
                f'No team matching "{name}". Existing teams: '
                + ", ".join(sorted(Team.objects.values_list("name", flat=True))[:25])
            )
        if len(matches) > 1:
            raise CommandError(
                f'"{name}" matches {len(matches)} teams: '
                + ", ".join(t.name for t in matches)
                + ". Use a more specific name."
            )
        return matches[0]

    # ── main ──────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        totals = self._parse_totals(opts["totals"])
        team = self._resolve_team(opts["team"])
        year = opts["year"]
        apply_changes = opts["apply"]
        allow_reduce = opts["allow_reduce"]

        member_ids = list(
            TeamMember.objects.filter(team=team).values_list("ir_id", flat=True)
        )
        if not member_ids:
            raise CommandError(f'Team "{team.name}" has no members, so it can have no UVs.')

        try:
            actor = Ir.objects.get(ir_id=opts["attribute_to"])
        except Ir.DoesNotExist:
            raise CommandError(f'No IR with ir_id {opts["attribute_to"]!r}')
        if actor.ir_id not in member_ids:
            raise CommandError(
                f'{actor.ir_id} ({actor.ir_name}) is not a member of "{team.name}". '
                "Adjustments must land on someone the team's totals actually count."
            )

        self.stdout.write(f'Team          : {team.name} (id={team.id}, {len(member_ids)} members)')
        self.stdout.write(f'Attributing to: {actor.ir_id} — {actor.ir_name}')
        self.stdout.write(f'Year          : {year}')
        self.stdout.write(f'Mode          : {"APPLY (writes)" if apply_changes else "dry run (no writes)"}')
        self.stdout.write("")

        plan = []
        for week, target in totals.items():
            _, _, start, end = get_week_info_friday_to_friday(week_number=week, year=year)

            existing_all = UVDetail.objects.filter(
                ir_id__in=member_ids, uv_date__gte=start, uv_date__lte=end
            )
            current = existing_all.aggregate(t=Sum("uv_count"))["t"] or Decimal("0")
            prior_adjustment = existing_all.filter(comments__contains=MARKER)
            prior_total = prior_adjustment.aggregate(t=Sum("uv_count"))["t"] or Decimal("0")
            # What the team recorded themselves, ignoring anything this command
            # wrote on an earlier run — that is what the adjustment must top up.
            genuine = current - prior_total
            needed = target - genuine

            plan.append({
                "week": week, "target": target, "current": current,
                "genuine": genuine, "prior": prior_total, "needed": needed,
                "start": start, "end": end, "prior_rows": list(prior_adjustment),
            })

        blocked = [p for p in plan if p["needed"] < 0]
        changes = [p for p in plan if p["needed"] != p["prior"]]

        hdr = f'{"Wk":>3}  {"target":>7}  {"now":>7}  {"theirs":>7}  {"adj":>7}   action'
        self.stdout.write(hdr)
        self.stdout.write("-" * len(hdr))
        for p in plan:
            if p["needed"] < 0:
                action = f'OVER by {-p["needed"]} — ' + ("will reduce" if allow_reduce else "SKIPPED")
            elif p["needed"] == p["prior"]:
                action = "already correct"
            elif p["prior"]:
                action = f'update adjustment {p["prior"]} -> {p["needed"]}'
            elif p["needed"] == 0:
                action = "already correct"
            else:
                action = f'add {p["needed"]}'
            self.stdout.write(
                f'{p["week"]:>3}  {p["target"]:>7}  {p["current"]:>7}  '
                f'{p["genuine"]:>7}  {p["needed"]:>7}   {action}'
            )

        self.stdout.write("")
        if blocked and not allow_reduce:
            self.stdout.write(self.style.WARNING(
                f'{len(blocked)} week(s) already total MORE than the figure given: '
                + ", ".join(str(p["week"]) for p in blocked)
                + ". Left untouched. Bringing them down means altering UVs somebody "
                  "recorded — re-run with --allow-reduce only if that is intended."
            ))

        if not apply_changes:
            self.stdout.write(self.style.NOTICE(
                f"Dry run — nothing written. {len(changes)} week(s) would change. "
                "Re-run with --apply once the table above looks right."
            ))
            return

        written = 0
        with transaction.atomic():
            for p in plan:
                if p["needed"] < 0 and not allow_reduce:
                    continue
                # One adjustment row per week: drop any this command wrote before
                # so the week never carries two of them.
                for row in p["prior_rows"]:
                    row.delete()

                remaining = p["target"] - p["genuine"]
                if remaining < 0:
                    if not allow_reduce:
                        continue
                    # Reducing below what the team genuinely recorded cannot be
                    # done with a positive top-up row; a negative row keeps the
                    # sum honest and stays visible and reversible.
                    self.stdout.write(self.style.WARNING(
                        f'  week {p["week"]}: writing a negative adjustment of {remaining} '
                        "to bring the total down"
                    ))
                if remaining == 0:
                    continue

                mid = p["start"] + (p["end"] - p["start"]) / 2
                UVDetail.objects.create(
                    ir=actor,
                    ir_name=actor.ir_name or "",
                    prospect_name="",
                    uv_date=mid,
                    uv_count=remaining,
                    comments=f'{MARKER} week {p["week"]} {p["year"] if "year" in p else year} '
                             f"reconciliation to a supplied total of {p['target']}",
                )
                written += 1

        self.stdout.write(self.style.SUCCESS(f"Wrote {written} adjustment row(s)."))
        self.stdout.write("Re-running is safe: it corrects these rows rather than adding more.")
