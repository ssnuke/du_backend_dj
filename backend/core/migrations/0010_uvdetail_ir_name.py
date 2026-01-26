# Generated migration for adding ir_name field to UVDetail

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_uvdetail'),
    ]

    operations = [
        migrations.AddField(
            model_name='uvdetail',
            name='ir_name',
            field=models.CharField(blank=True, default='', max_length=45),
        ),
    ]
