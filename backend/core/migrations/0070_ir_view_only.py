from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0069_ir_chat_only'),
    ]

    operations = [
        migrations.AddField(
            model_name='ir',
            name='view_only',
            field=models.BooleanField(default=False),
        ),
    ]
