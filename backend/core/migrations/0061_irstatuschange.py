import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0060_plandetail_location'),
    ]

    operations = [
        migrations.CreateModel(
            name='IrStatusChange',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ir_id_text', models.CharField(max_length=18)),
                ('ir_name', models.CharField(max_length=45)),
                ('is_active', models.BooleanField()),
                ('changed_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='status_changes_made', to='core.ir')),
                ('ir', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='status_changes', to='core.ir')),
            ],
        ),
    ]
