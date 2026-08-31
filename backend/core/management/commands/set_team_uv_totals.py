"""
Reconcile a team's weekly UV totals to a supplied set of figures.

Written because UV history for past weeks was recorded outside the app and has
to be brought in line week by week. It is a reconciliation, not an import: for
each week it works out what the team currently totals and writes only the
difference, so a week that is already correct is left completely alone.
"""
import os
import re
from decimal import Decimal, ROUND_HALF_UP

from contextlib import contextmanager

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.db.models import Sum

from core.models import Ir, Team, TeamMember, TeamRole, UVDetail
from core.utils.dates import get_week_info_friday_to_friday

# Every row this command creates carries this marker in its comments. It is
# what makes a re-run adjust the row it wrote last time instead of stacking a
# second one on top — without it, running twice would double the correction.
MARKER = "[uv-backfill]"


@contextmanager
def signals_muted():
    """
    Silence UV notifications for the duration of a backfill.

    core/signals.py fires a push notification to the uplines on every
    UVDetail save AND delete. That is right for somebody entering a UV today
    and completely wrong here: reconciling a year of history sent hundreds of
    "New UV Record Added" alerts for UVs dated months ago, to real people's
    phones. Backfilled history is not news.
    """
    from core import signals

    muted = [
        (post_save, signals.notify_uv_saved),
        (post_delete, signals.notify_uv_deleted),
    ]
    for sig, fn in muted:
        sig.disconnect(fn, sender=UVDetail)
    try:
        yield
    finally:
        for sig, fn in muted:
            sig.connect(fn, sender=UVDetail)

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
        parser.add_argument("--team", default=None,
                            help='Only needed when the input has no team headings. Otherwise every '
                                 'team named in the file is processed in one run.')
        parser.add_argument("--year", type=int, required=True, help="Year the week numbers belong to.")
        parser.add_argument("--attribute-to", default="ldc",
                            help="Who adjustment rows are recorded against. UVs belong to a person, "
                                 "not a team, so this IR's own weekly numbers will include the "
                                 "adjustment. Either an ir_id, or \"ldc\" (the default) to use the "
                                 "team's own LDC. Must be a member of the team either way.")
        parser.add_argument("--totals", required=True,
                            help="Path to a file of team blocks — a team name on its own line, its "
                                 "week totals beneath it, blank line between teams. Accepts "
                                 '"Week 1 : 0", "Week - 1 : 6", "Week 1: 0" and "Week 1 - 0" '
                                 "interchangeably. A bare list of pairs also works with --team.")
        parser.add_argument("--teams", default=None,
                            help="Comma-separated team names to treat as ONE unit, for a figure set "
                                 "that covers several teams at once — an LDC's whole group rather "
                                 "than a single team. The weekly figure is matched against the "
                                 "combined total across all of them. Requires a single block of "
                                 "totals.")
        parser.add_argument("--distribute", action="store_true",
                            help="Spread each week's adjustment across the group's teams instead of "
                                 "placing it on one person. Required when the group's teams share "
                                 "members: the app totals a group by SUMMING each team separately, "
                                 "so a row under someone in 3 of the teams is counted 3 times. Each "
                                 "share lands on a member of exactly one team, and the remainder is "
                                 "allocated so the group total is exact.")
        parser.add_argument("--apply", action="store_true",
                            help="Actually write. Without this, only reports what would change.")
        parser.add_argument("--allow-reduce", action="store_true",
                            help="Permit reducing a week that is already above its supplied figure. "
                                 "This edits or deletes rows other people entered.")

    # ── inputs ────────────────────────────────────────────────────────────
    # Tolerates every shape the figures actually arrive in:
    #   "Week 1 : 0"  "Week - 1 : 6"  "Week 1: 0"  "Week 1 - 0"  "1:0"
    WEEK_LINE = re.compile(r"^\s*(?:week\s*-?\s*)?(\d+)\s*[:\-]\s*([0-9]*\.?[0-9]+)\s*$", re.I)

    def _parse_blocks(self, raw, default_team=None):
        """
        Parse one or more teams out of a pasted block.

        A line that is not a week line and not blank starts a new team, so the
        figures can be pasted exactly as they are sent — team name on its own
        line, its weeks beneath it — rather than being retyped into some
        other format and mistranscribed on the way.

        Returns {team_name: {week: Decimal}}.
        """
        text = raw
        if isinstance(raw, str) and os.path.isfile(raw):
            with open(raw) as fh:
                text = fh.read()
        elif isinstance(raw, str) and ":" not in raw and "-" not in raw:
            raise CommandError(f"{raw!r} is neither a file nor a list of week totals")

        blocks, current = {}, default_team
        for lineno, line in enumerate(text.replace(",", "\n").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            m = self.WEEK_LINE.match(line)
            if m:
                if current is None:
                    raise CommandError(f"line {lineno}: week total {line!r} before any team name")
                week, total = int(m.group(1)), Decimal(m.group(2))
                if week < 1:
                    raise CommandError(f"line {lineno}: week {week} is not a real week")
                prev = blocks.setdefault(current, {}).get(week)
                if prev is not None and prev != total:
                    raise CommandError(
                        f"line {lineno}: {current} week {week} given twice "
                        f"({prev} then {total})"
                    )
                blocks[current][week] = total
                continue
            # not a week line -> a team heading. Trailing ':' or '-' is noise.
            current = line.rstrip(":- ").strip()
            if not current:
                raise CommandError(f"line {lineno}: blank team name")
            blocks.setdefault(current, {})

        empty = [t for t, w in blocks.items() if not w]
        if empty:
            raise CommandError("No week totals given for: " + ", ".join(empty))
        if not blocks:
            raise CommandError("No week totals found")
        return {t: dict(sorted(w.items())) for t, w in blocks.items()}

    def _team_anchors(self, teams):
        """
        One IR per team who belongs to exactly ONE team of this group, so a row
        written against them is counted once by the app's per-team sum. Most
        senior first, purely so the adjustment sits somewhere sensible.
        """
        from collections import Counter
        counts = Counter()
        per_team = {}
        for t in teams:
            ids = list(TeamMember.objects.filter(team=t).values_list("ir_id", flat=True))
            per_team[t.id] = ids
            counts.update(set(ids))

        anchors = {}
        for t in teams:
            solo = [i for i in per_team[t.id] if counts[i] == 1]
            if not solo:
                raise CommandError(
                    f'Every member of "{t.name}" also belongs to another team in this group, '
                    "so there is nobody who would be counted only once. "
                    "Pass --attribute-to <ir_id> and drop --distribute."
                )
            anchors[t.id] = (
                Ir.objects.filter(ir_id__in=solo).order_by("ir_access_level", "ir_name").first()
            )
        return anchors

    # UVs are only ever counted in halves — a third of a UV is not a thing that
    # exists in the business, so shares are allocated in 0.5 steps.
    STEP = Decimal("0.5")

    @classmethod
    def _split(cls, amount, n):
        """
        Split `amount` into n parts that are each a multiple of 0.5 and sum
        EXACTLY back to it.

        Dividing straight gives thirds — 10 over 3 teams became 3.33/3.33/3.34,
        which is not a real UV figure. Working in half-units instead gives
        3.5/3.5/3.0: still exact, and every part a number the business uses.
        """
        units = amount / cls.STEP
        if units != units.to_integral_value():
            raise CommandError(
                f"{amount} is not a multiple of 0.5, so it cannot be split into "
                "whole half-UVs. Check the supplied figure and the existing data."
            )
        units = int(units)
        base, extra = divmod(abs(units), n)
        sign = -1 if units < 0 else 1
        parts = [sign * (base + (1 if i < extra else 0)) for i in range(n)]
        out = [Decimal(p) * cls.STEP for p in parts]
        assert sum(out) == amount, (out, amount)
        return out

    def _resolve_actor(self, spec, teams):
        """
        Who the adjustment rows belong to. "ldc" looks up the LDC of the teams
        involved rather than making the caller find an ir_id by hand and risk
        pinning a year of corrections on the wrong person — a real hazard when
        two people share a first name.

        Across a group, every team must point at the same single LDC; anything
        else is ambiguous and has to be stated explicitly.
        """
        if spec.strip().lower() != "ldc":
            try:
                return Ir.objects.get(ir_id=spec)
            except Ir.DoesNotExist:
                raise CommandError(f"No IR with ir_id {spec!r}")

        ldc_ids = set(
            TeamMember.objects.filter(team__in=teams, role=TeamRole.LDC)
                              .values_list("ir_id", flat=True)
        )
        if len(ldc_ids) == 1:
            return Ir.objects.get(ir_id=ldc_ids.pop())

        label = " + ".join(t.name for t in teams)
        if not ldc_ids:
            creators = {t.created_by_id for t in teams if t.created_by_id}
            if len(creators) == 1:
                return Ir.objects.get(ir_id=creators.pop())
            raise CommandError(
                f'No LDC recorded on "{label}". Pass --attribute-to <ir_id> explicitly.'
            )
        found = Ir.objects.filter(ir_id__in=ldc_ids)
        raise CommandError(
            f'"{label}" has {len(ldc_ids)} LDCs: '
            + ", ".join(f"{i.ir_id} ({i.ir_name})" for i in found)
            + ". Pass --attribute-to <ir_id> to choose."
        )

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
        blocks = self._parse_blocks(opts["totals"], default_team=opts["team"])

        # Resolve EVERY team before writing anything. A typo in the fifth
        # team's name should not be discovered after the first four have
        # already been written.
        resolved = []
        if opts["teams"]:
            # One figure set spanning several teams — an LDC's whole group.
            if len(blocks) != 1:
                raise CommandError(
                    "--teams treats one set of figures as spanning several teams, so the "
                    f"input must hold exactly one block; found {len(blocks)}: "
                    + ", ".join(blocks)
                )
            label, totals = next(iter(blocks.items()))
            teams = [self._resolve_team(n) for n in opts["teams"].split(",") if n.strip()]
            if not teams:
                raise CommandError("--teams was empty")
            resolved.append((label, teams, self._resolve_actor(opts["attribute_to"], teams), totals))
        else:
            for name, totals in blocks.items():
                team = self._resolve_team(name)
                resolved.append((team.name, [team],
                                 self._resolve_actor(opts["attribute_to"], [team]), totals))

        self.stdout.write(f'{len(resolved)} group(s) to process, year {opts["year"]}')
        self.stdout.write(
            f'Mode: {"APPLY (writes)" if opts["apply"] else "dry run (no writes)"}\n'
        )

        grand_written = 0
        for label, teams, actor, totals in resolved:
            grand_written += self._process_team(label, teams, actor, totals, opts)

        if opts["apply"]:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone. {grand_written} adjustment row(s) across {len(resolved)} team(s)."
            ))
            self.stdout.write("Re-running is safe: it corrects these rows rather than adding more.")
        else:
            self.stdout.write(self.style.NOTICE(
                "\nDry run — nothing written. Re-run with --apply once the tables look right."
            ))

    def _process_team(self, label, teams, actor, totals, opts):
        year = opts["year"]
        apply_changes = opts["apply"]
        allow_reduce = opts["allow_reduce"]

        # Deduped across the group: somebody in two of an LDC's teams must not
        # have their UVs counted twice toward the group's weekly total.
        member_ids = list(set(
            TeamMember.objects.filter(team__in=teams).values_list("ir_id", flat=True)
        ))
        if not member_ids:
            raise CommandError(f'"{label}" has no members, so it can have no UVs.')
        if actor.ir_id not in member_ids:
            raise CommandError(
                f'{actor.ir_id} ({actor.ir_name}) is in none of the teams making up "{label}". '
                "Adjustments must land on someone whose UVs the group total actually counts."
            )

        made_of = "" if len(teams) == 1 else "  [" + " + ".join(t.name for t in teams) + "]"
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n{label}  —  {len(member_ids)} people, weeks '
            f'{min(totals)}-{max(totals)}, under {actor.ir_name}{made_of}'
        ))

        # How the app totals a group: each team summed on its own, then added.
        # Somebody in two of the teams is therefore counted twice, and matching
        # that is the whole point — a deduplicated union agrees with itself but
        # not with the number on screen.
        distribute = opts["distribute"]
        team_member_ids = {
            t.id: list(TeamMember.objects.filter(team=t).values_list("ir_id", flat=True))
            for t in teams
        }
        anchors = self._team_anchors(teams) if distribute else None

        def window_total(start, end, only=None):
            rows = UVDetail.objects.filter(uv_date__gte=start, uv_date__lte=end)
            if only is not None:
                rows = rows.filter(comments__contains=MARKER) if only == "marked" else rows
            return sum(
                (rows.filter(ir_id__in=ids).aggregate(t=Sum("uv_count"))["t"] or Decimal("0"))
                for ids in team_member_ids.values()
            )

        plan = []
        for week, target in totals.items():
            _, _, start, end = get_week_info_friday_to_friday(week_number=week, year=year)

            existing_all = UVDetail.objects.filter(
                ir_id__in=member_ids, uv_date__gte=start, uv_date__lte=end
            )
            current = window_total(start, end)
            prior_adjustment = existing_all.filter(comments__contains=MARKER)
            prior_total = window_total(start, end, only="marked")
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
            self.stdout.write(f"  {len(changes)} week(s) would change.")
            return 0

        written = 0
        with signals_muted(), transaction.atomic():
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
                if distribute:
                    # One share per team, each on somebody counted only once,
                    # remainders allocated so the group total is exact.
                    shares = self._split(remaining, len(teams))
                    for t, share in zip(teams, shares):
                        if share == 0:
                            continue
                        who = anchors[t.id]
                        UVDetail.objects.create(
                            ir=who,
                            ir_name=who.ir_name or "",
                            prospect_name="UV backfill adjustment",
                            uv_date=mid,
                            uv_count=share,
                            comments=f'{MARKER} week {p["week"]} {year} '
                                     f"reconciliation to a supplied total of {p['target']} "
                                     f"({t.name} share)",
                        )
                        written += 1
                    continue

                UVDetail.objects.create(
                    ir=actor,
                    ir_name=actor.ir_name or "",
                    # These rows appear in UV lists on the dashboard like any
                    # other, so they say what they are rather than showing up
                    # as an unexplained blank — especially the negative ones.
                    prospect_name="UV backfill adjustment",
                    uv_date=mid,
                    uv_count=remaining,
                    comments=f'{MARKER} week {p["week"]} {p["year"] if "year" in p else year} '
                             f"reconciliation to a supplied total of {p['target']}",
                )
                written += 1

        self.stdout.write(f"  wrote {written} adjustment row(s).")
        return written
