from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0056_alter_learnvideo_category'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatroom',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
    ]
