# Generated migration for chat room enhancements

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0025_chat_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatroom',
            name='is_pinned',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='chatroom',
            name='pinned_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='chatroom',
            name='category',
            field=models.CharField(
                choices=[
                    ('group', 'Group'),
                    ('activities', 'Activities'),
                    ('spocs', 'Spocs'),
                    ('personal', 'Personal'),
                    ('relentless', 'Relentless'),
                    ('general', 'General')
                ],
                default='group',
                max_length=50
            ),
        ),
        migrations.AddIndex(
            model_name='chatroom',
            index=models.Index(fields=['-is_pinned', '-pinned_at'], name='core_chatro_is_pinn_46883f_idx'),
        ),
        migrations.AddIndex(
            model_name='chatroom',
            index=models.Index(fields=['category', '-updated_at'], name='core_chatro_categor_be11fb_idx'),
        ),
        migrations.AlterModelOptions(
            name='chatroom',
            options={'ordering': ['-is_pinned', '-pinned_at', '-updated_at']},
        ),
    ]
