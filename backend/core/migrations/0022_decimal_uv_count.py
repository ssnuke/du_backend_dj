from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_decimal_uv_targets"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ir",
            name="uv_count",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
    ]
