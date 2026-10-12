from django.db import migrations, models

LESSON_CHOICES = [
    ('welcome', 'Welcome'), ('why_nm', 'Why NM'), ('switch_on', 'Switch ON'),
    ('info_part_1', 'Info Part 1'), ('info_part_2', 'Info Part 2'), ('oh1', 'OH1'),
    ('scripts', 'Scripts'), ('closings', 'Closings'), ('prospecting', 'Prospecting'),
    ('success_formula', 'Success Formula'),
]


def carry_over_assignments(apps, schema_editor):
    """
    The outline changed: "Info Part" became "Info Part 1" (with a new "Info Part
    2" after it) and OH2 was dropped. A video already placed in either keeps
    working: the old Info Part becomes Part 1, and a video that sat in OH2 is
    simply unassigned (it still exists and shows after the course until placed).
    """
    LearnVideo = apps.get_model('core', 'LearnVideo')
    LearnVideo.objects.filter(lesson_key='info_part').update(lesson_key='info_part_1')
    LearnVideo.objects.filter(lesson_key='oh2').update(lesson_key=None)


def undo(apps, schema_editor):
    LearnVideo = apps.get_model('core', 'LearnVideo')
    LearnVideo.objects.filter(lesson_key='info_part_1').update(lesson_key='info_part')
    # Part 2 and the dropped OH2 have no equivalent in the old outline.
    LearnVideo.objects.filter(lesson_key='info_part_2').update(lesson_key=None)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0065_promotiondocument_uploaded_by'),
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
        migrations.RunPython(carry_over_assignments, undo),
    ]
