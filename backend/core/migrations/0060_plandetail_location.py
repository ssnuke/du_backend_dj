from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0059_infodetail_location'),
    ]

    operations = [
        migrations.AddField(
            model_name='plandetail',
            name='location',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
    ]
