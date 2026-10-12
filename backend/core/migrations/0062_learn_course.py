import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0061_irstatuschange'),
    ]

    operations = [
        migrations.AddField(
            model_name='learnvideo',
            name='lesson_key',
            field=models.CharField(
                blank=True, null=True, unique=True, max_length=20,
                choices=[
                    ('welcome', 'Welcome'), ('why_nm', 'Why NM'), ('switch_on', 'Switch ON'),
                    ('info_part', 'Info Part'), ('oh1', 'OH1'), ('oh2', 'OH2'), ('scripts', 'Scripts'),
                    ('closings', 'Closings'), ('prospecting', 'Prospecting'), ('success_formula', 'Success Formula'),
                ],
                help_text='Basic Training course slot. Leave empty for videos outside the course.',
            ),
        ),
        migrations.CreateModel(
            name='LearnVideoProgress',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('completed_at', models.DateTimeField(auto_now_add=True)),
                ('ir', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='learn_progress', to='core.ir')),
                ('video', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='completions', to='core.learnvideo')),
            ],
            options={'unique_together': {('ir', 'video')}},
        ),
    ]
