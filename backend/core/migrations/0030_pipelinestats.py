from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_chat_features'),
    ]

    operations = [
        migrations.CreateModel(
            name='PipelineStats',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('total_name_list', models.IntegerField(default=0)),
                ('numbers_have', models.IntegerField(default=0)),
                ('numbers_dont_have', models.IntegerField(default=0)),
                ('infos_done', models.IntegerField(default=0)),
                ('infos_not_done', models.IntegerField(default=0)),
                ('invites_gone', models.IntegerField(default=0)),
                ('invites_not_gone', models.IntegerField(default=0)),
                ('invite_yes', models.IntegerField(default=0)),
                ('invite_no', models.IntegerField(default=0)),
                ('showed_up', models.IntegerField(default=0)),
                ('didnt_show_up', models.IntegerField(default=0)),
                ('signed_up_dr', models.IntegerField(default=0)),
                ('kiv', models.IntegerField(default=0)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('last_sync', models.DateTimeField(blank=True, null=True)),
                ('ir', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pipeline_stats',
                    to='core.ir',
                )),
            ],
            options={
                'verbose_name': 'Pipeline Stats',
                'verbose_name_plural': 'Pipeline Stats',
            },
        ),
    ]
