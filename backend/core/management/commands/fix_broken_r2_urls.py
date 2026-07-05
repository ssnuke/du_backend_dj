from django.core.management.base import BaseCommand

from core.models import ChatMessage, ChatRoom, Ir, Sticker

_BROKEN = "https://https://"
_FIXED = "https://"

# (model, field name) pairs that can hold a stored delivery URL from
# default_storage.url() — the only fields affected by the doubled-scheme
# R2_PUBLIC_URL bug (see config/settings.py's AWS_S3_CUSTOM_DOMAIN comment).
_TARGETS = [
    (Ir, "avatar_url"),
    (ChatRoom, "image_url"),
    (ChatMessage, "attachment_url"),
    (Sticker, "image_url"),
]


class Command(BaseCommand):
    help = (
        "One-off repair for URLs stored with a doubled 'https://https://' "
        "prefix — a bug in the R2 migration (fixed in config/settings.py) "
        "that affected every upload between the R2 cutover and that fix. "
        "Safe to run repeatedly; only touches rows matching the broken "
        "pattern."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report how many rows would change without saving anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        total = 0

        for model, field in _TARGETS:
            filter_kwargs = {f"{field}__startswith": _BROKEN}
            rows = list(model.objects.filter(**filter_kwargs))
            if not rows:
                self.stdout.write(f"{model.__name__}.{field}: 0 broken rows")
                continue

            for row in rows:
                value = getattr(row, field)
                setattr(row, field, value.replace(_BROKEN, _FIXED, 1))

            if not dry_run:
                model.objects.bulk_update(rows, [field])

            total += len(rows)
            self.stdout.write(f"{model.__name__}.{field}: {len(rows)} broken row(s) {'found' if dry_run else 'fixed'}")

        verb = "would be fixed" if dry_run else "fixed"
        self.stdout.write(self.style.SUCCESS(f"Total: {total} row(s) {verb}."))
