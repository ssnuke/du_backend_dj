from django.db import migrations, models

LESSON_CHOICES = [
    ('welcome', 'Welcome'), ('why_nm', 'Why NM'), ('switch_on', 'Switch ON'),
    ('info_part_1', 'Info Part 1'), ('info_part_2', 'Info Part 2'), ('oh1', 'OH1'),
    ('closings', 'Closings'), ('prospecting', 'Prospecting'), ('success_formula', 'Success Formula'),
]


def unassign_scripts(apps, schema_editor):
    """
    The Scripts lesson became a scripts-only reference with no video, so a video
    that had been placed in it is unassigned. It still exists and shows after
    the course until it is placed in a video lesson.
    """
    apps.get_model('core', 'LearnVideo').objects.filter(lesson_key='scripts').update(lesson_key=None)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0066_course_outline_info_parts'),
    ]

    operations = [
        migrations.AlterField(
            model_name='learnvideo',
            name='lesson_key',
            field=models.CharField(
                blank=True, null=True, unique=True, max_length=20, choices=LESSON_CHOICES,
                help_text='Basic Training course slot. Leave empty for videos outside the course.',
            ),
        ),
        migrations.RunPython(unassign_scripts, migrations.RunPython.noop),
    ]
