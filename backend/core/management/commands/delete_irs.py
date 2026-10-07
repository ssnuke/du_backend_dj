"""
Delete a list of IRs safely — command-line twin of the Admin API
(POST /api/admin/delete_irs/). Both call core/utils/ir_deletion.py, which
explains the checks. Dry run by default; the dry run is a full rehearsal
(the real delete, verified, then rolled back).

    python manage.py delete_irs --file irs.txt                        # rehearsal
    python manage.py delete_irs --file irs.txt --apply --confirm-count 21

File format: one IR per line, ID then name — a pasted table works as-is.
"""
from django.core.management.base import BaseCommand, CommandError

from core.models import AccessLevel
from core.utils import ir_deletion as d

# Re-exported: tests (and older callers) import these from here.
parse_targets = d.parse_targets
impact = d.impact


class Command(BaseCommand):
    help = "Delete a list of IRs (rehearsal unless --apply --confirm-count N)."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True, help="Text file: one 'IM123456 Name' per line.")
        parser.add_argument("--apply", action="store_true", help="Actually delete. Default is a rehearsal.")
        parser.add_argument("--confirm-count", type=int, default=None,
                            help="With --apply: must equal the number of IRs in the file.")
        parser.add_argument("--allow-leaders", action="store_true",
                            help="Permit deleting Admin/CTC/LDC accounts (refused by default).")
        parser.add_argument("--quiet", action="store_true",
                            help="Also suppress the per-IR 'IR Deleted' notification to uplines.")

    def handle(self, *args, **opts):
        try:
            with open(opts["file"], encoding="utf-8") as fh:
                targets = d.parse_targets(fh.read())
        except OSError as exc:
            raise CommandError(f"Cannot read {opts['file']}: {exc}")
        if not targets:
            raise CommandError("No 'IM<digits> <name>' lines found in the file")

        resolved, problems = d.resolve_targets(targets, allow_leaders=opts["allow_leaders"])
        delete_ids = {ir.ir_id for ir in resolved}
        self.stdout.write(f"{len(targets)} in file, {len(resolved)} resolved, {len(problems)} problem(s)\n")

        for ir in sorted(resolved, key=lambda i: i.ir_id):
            info = d.impact(ir, delete_ids)
            parent = ir.parent_ir
            self.stdout.write(
                f"\n{ir.ir_id}  {ir.ir_name}  [{AccessLevel.get_role_name(ir.ir_access_level)}, "
                f"{'active' if ir.status else 'inactive'}]  referrer: "
                f"{parent.ir_id + ' ' + parent.ir_name if parent else 'none'}"
            )
            self.stdout.write(
                f"  removes: {info['infos']} infos, {info['plans']} plans, "
                f"{info['uv_records']} UV records (total {info['uv_total']}), "
                f"{info['messages']} chat messages, {info['team_memberships']} team memberships, "
                f"{info['pocket_memberships']} pocket memberships"
            )
            if info["groups_owned"]:
                self.stdout.write(f"  ! owns {info['groups_owned']} chat group(s) — will be marked INACTIVE (read-only), history kept")
            if info["teams_created"]:
                self.stdout.write(f"  ! created {info['teams_created']} team(s) — kept, creator cleared")
            if info["pockets_created"]:
                self.stdout.write(f"  ! created {info['pockets_created']} pocket(s) — kept, creator cleared")

        if problems:
            self.stdout.write("\nPROBLEMS:")
            for p in problems:
                self.stdout.write(f"  x {p}")
            raise CommandError("Fix the problems above first (nothing deleted).")

        if opts["apply"] and opts["confirm_count"] != len(resolved):
            raise CommandError(f"--confirm-count must equal {len(resolved)} to apply (nothing deleted).")

        result = d.run(resolved, apply=opts["apply"], quiet=opts["quiet"])

        self.stdout.write("\nTREE — these IRs change parent:")
        for r in result["reparented"]:
            self.stdout.write(f"  {r['ir_id']} {r['ir_name']}  ->  "
                              f"{(r['new_parent_id'] + ' ' + r['new_parent_name']) if r['new_parent_id'] else 'no parent (becomes a root)'}")
        if not result["reparented"]:
            self.stdout.write("  (none)")
        if result["groups_marked_inactive"]:
            self.stdout.write(f"\nGroups marked inactive: {', '.join(g['room_name'] for g in result['groups_marked_inactive'])}")

        checks = result["checks"]
        self.stdout.write(f"\nChecks: tree {'OK' if not (checks['parent_mismatches'] or checks['new_hierarchy_violations']) else 'FAILED'}, "
                          f"collateral {'OK' if not checks['collateral_changes'] else 'FAILED'}"
                          f" (pre-existing tree problems elsewhere: {checks['preexisting_hierarchy_violations']})")
        if not result["ok"]:
            self.stdout.write(str(checks))
            raise CommandError(f"{result['mode']} — nothing was deleted.")

        if result["mode"] == "dry_run":
            self.stdout.write(f"\nREHEARSAL OK — nothing deleted. Re-run with --apply --confirm-count {len(resolved)} to delete.")
            return
        self.stdout.write(self.style.SUCCESS(f"\nDeleted {len(result['deleted'])} IR(s):"))
        for row in result["deleted"]:
            self.stdout.write(f"  {row['ir_id']} {row['ir_name']}")
