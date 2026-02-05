# Generated migration for changing UVDetail.uv_count from IntegerField to DecimalField

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_notification'),
    ]

    operations = [
        migrations.AlterField(
            model_name='uvdetail',
            name='uv_count',
            field=models.DecimalField(decimal_places=2, default=1, max_digits=10),
        ),
    ]
