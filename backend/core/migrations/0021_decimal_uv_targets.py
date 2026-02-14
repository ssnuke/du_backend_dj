from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_ir_fcm_tokens"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ir",
            name="weekly_uv_target",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name="weeklytarget",
            name="ir_weekly_uv_target",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name="weeklytarget",
            name="team_weekly_uv_target",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name="weeklytarget",
            name="pocket_weekly_uv_target",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
    ]
