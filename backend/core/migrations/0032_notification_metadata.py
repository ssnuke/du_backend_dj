from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_infodetail_monitored_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='metadata',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
