from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0030_pipelinestats'),
    ]

    operations = [
        migrations.AddField(
            model_name='infodetail',
            name='monitored_by',
            field=models.CharField(blank=True, default='Self', max_length=100, null=True),
        ),
    ]
