# Generated migration for adding prospect_name field to UVDetail

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_uvdetail_ir_name'),
    ]

    operations = [
        migrations.AddField(
            model_name='uvdetail',
            name='prospect_name',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
