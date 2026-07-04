# This migration originally re-created the Pocket/PocketMember models (already
# created by 0012_pocketmember_infodetail_info_type_and_more) and swapped
# WeeklyTarget's unique_together for named UniqueConstraints. The current
# models.py still uses unique_together (see WeeklyTarget.Meta), so that swap
# was never adopted and this migration's operations only duplicated state
# from 0012 — which breaks a from-scratch `migrate` with "relation already
# exists". Left as a no-op so already-migrated databases (where this was
# recorded as applied) are unaffected, while fresh installs/test DBs no
# longer replay the duplicate CreateModel calls.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_pocketmember_infodetail_info_type_and_more'),
    ]

    operations = []
