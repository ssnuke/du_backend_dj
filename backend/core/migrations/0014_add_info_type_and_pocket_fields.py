# This migration originally re-added info_type/pocket fields that were already
# added by 0012_pocketmember_infodetail_info_type_and_more, which breaks a
# from-scratch `migrate` with "column already exists". The only real state
# change on top of 0012 is that info_type ended up nullable/blank in
# models.py, so this is trimmed to just that AlterField; the duplicate
# AddField calls for the already-existing fields have been removed.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_pockets_and_targets'),
    ]

    operations = [
        migrations.AlterField(
            model_name='infodetail',
            name='info_type',
            field=models.CharField(
                choices=[('Fresh', 'Fresh'), ('Re-info', 'Reinfo')],
                default='Fresh',
                max_length=10,
                null=True,
                blank=True,
            ),
        ),
    ]
