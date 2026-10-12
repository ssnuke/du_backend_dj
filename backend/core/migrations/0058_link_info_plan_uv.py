import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0057_chatroom_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='plandetail',
            name='info',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='plans', to='core.infodetail',
            ),
        ),
        migrations.AddField(
            model_name='uvdetail',
            name='plan',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='uv_records', to='core.plandetail',
            ),
        ),
    ]
