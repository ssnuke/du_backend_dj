from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0033_pipelinestats_new_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='LearnVideo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
                ('bunny_video_id', models.CharField(max_length=100, unique=True)),
                ('bunny_library_id', models.CharField(max_length=100)),
                ('thumbnail_url', models.URLField(blank=True)),
                ('duration_seconds', models.IntegerField(default=0)),
                ('order', models.IntegerField(default=0, help_text='Sort order in the list')),
                ('is_published', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Learn Video',
                'verbose_name_plural': 'Learn Videos',
                'ordering': ['order', 'created_at'],
            },
        ),
    ]
