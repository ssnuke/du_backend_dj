import boto3
from botocore.config import Config
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

TARGET_CACHE_CONTROL = "public, max-age=31536000, immutable"


class Command(BaseCommand):
    help = (
        "One-off backfill: sets Cache-Control on every object already in the R2 "
        "bucket, matching what config.settings.AWS_S3_OBJECT_PARAMETERS now applies "
        "to new uploads automatically. Files uploaded before that setting was added "
        "have no Cache-Control header at all, so browsers re-download them in full "
        "on every view instead of ever caching them — confirmed via a live Network "
        "tab check showing repeated full-size 200s, not disk-cache hits.\n\n"
        "Defaults to a dry run (reports what would change, touches nothing) — pass "
        "--apply to actually rewrite object metadata. This makes a real S3 "
        "copy_object API call per object that needs updating, against production "
        "storage, so review the dry-run output first."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually rewrite object metadata. Without this, only reports what would change.",
        )

    def _client(self):
        missing = [
            name for name in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_ENDPOINT_URL")
            if not getattr(settings, name, None)
        ]
        if missing:
            raise CommandError(f"Missing required R2 settings: {', '.join(missing)}")

        return boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(s3={"addressing_style": "virtual"}),
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        client = self._client()
        bucket = settings.R2_BUCKET_NAME

        checked = 0
        already_correct = 0
        updated = 0
        failed = 0

        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                checked += 1

                try:
                    head = client.head_object(Bucket=bucket, Key=key)
                except Exception as e:
                    failed += 1
                    self.stderr.write(f"  head_object failed for {key}: {e}")
                    continue

                if head.get("CacheControl") == TARGET_CACHE_CONTROL:
                    already_correct += 1
                    continue

                if apply_changes:
                    try:
                        client.copy_object(
                            Bucket=bucket,
                            Key=key,
                            CopySource={"Bucket": bucket, "Key": key},
                            MetadataDirective="REPLACE",
                            CacheControl=TARGET_CACHE_CONTROL,
                            ContentType=head.get("ContentType", "application/octet-stream"),
                            Metadata=head.get("Metadata", {}),
                        )
                        updated += 1
                    except Exception as e:
                        failed += 1
                        self.stderr.write(f"  copy_object failed for {key}: {e}")
                        continue
                else:
                    updated += 1  # "would update" count in dry-run mode

                if checked % 100 == 0:
                    self.stdout.write(f"  ...{checked} objects checked so far")

        verb = "updated" if apply_changes else "would be updated (dry run)"
        self.stdout.write(self.style.SUCCESS(
            f"Checked {checked} object(s): {already_correct} already correct, "
            f"{updated} {verb}, {failed} failed."
        ))
        if not apply_changes and updated:
            self.stdout.write("Re-run with --apply to actually make these changes.")
