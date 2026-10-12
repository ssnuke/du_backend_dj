from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0062_learn_course'),
    ]

    operations = [
        migrations.AddField(
            model_name='learnvideoprogress',
            name='position_seconds',
            field=models.FloatField(default=0),
        ),
        migrations.AddField(
            model_name='learnvideoprogress',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
        # Was auto_now_add (a row existed only once finished). Rows that exist
        # today are all completions, so they keep the time they were made.
        migrations.AlterField(
            model_name='learnvideoprogress',
            name='completed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
