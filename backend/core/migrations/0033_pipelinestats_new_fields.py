from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_notification_metadata'),
    ]

    operations = [
        # New contacts added — feeds into total_name_list
        migrations.AddField(
            model_name='pipelinestats',
            name='new_contacts',
            field=models.IntegerField(default=0),
        ),
        # Numbers extracted from don't-have → have
        migrations.AddField(
            model_name='pipelinestats',
            name='numbers_extracted',
            field=models.IntegerField(default=0),
        ),
        # Infos A / B / C breakdown of infos_done
        migrations.AddField(
            model_name='pipelinestats',
            name='infos_a',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='pipelinestats',
            name='infos_b',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='pipelinestats',
            name='infos_c',
            field=models.IntegerField(default=0),
        ),
    ]
