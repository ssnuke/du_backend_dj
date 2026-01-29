# Migration to add missing fields that should have been in 0012
# Adds info_type to InfoDetail and pocket fields to WeeklyTarget
# This migration handles fields that were skipped when 0012 had duplicate table errors

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_pockets_and_targets'),
    ]

    operations = [
        # Add info_type field to InfoDetail
        migrations.AddField(
            model_name='infodetail',
            name='info_type',
            field=models.CharField(
                choices=[('Fresh', 'Fresh'), ('Re-info', 'Reinfo')],
                default='Fresh',
                max_length=10
            ),
        ),
        # Add pocket field to WeeklyTarget (FK to Pocket)
        migrations.AddField(
            model_name='weeklytarget',
            name='pocket',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='weekly_targets',
                to='core.pocket'
            ),
        ),
        # Add pocket target fields to WeeklyTarget
        migrations.AddField(
            model_name='weeklytarget',
            name='pocket_weekly_info_target',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='weeklytarget',
            name='pocket_weekly_plan_target',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='weeklytarget',
            name='pocket_weekly_uv_target',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]

