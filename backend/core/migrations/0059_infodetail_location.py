from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0058_link_info_plan_uv'),
    ]

    operations = [
        migrations.AddField(
            model_name='infodetail',
            name='location',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
    ]
