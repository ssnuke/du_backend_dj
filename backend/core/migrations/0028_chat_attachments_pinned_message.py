from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_chatmessage_reply_edit_delete"),
    ]

    operations = [
        # attachment fields on ChatMessage
        migrations.AddField(
            model_name="chatmessage",
            name="attachment_url",
            field=models.URLField(blank=True, max_length=1000, null=True),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="attachment_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="attachment_size",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="chatmessage",
            name="attachment_duration",
            field=models.FloatField(blank=True, null=True),
        ),
        # Extend message_type choices
        migrations.AlterField(
            model_name="chatmessage",
            name="message_type",
            field=models.CharField(
                choices=[
                    ("text", "Text"),
                    ("image", "Image"),
                    ("video", "Video"),
                    ("file", "File"),
                    ("voice", "Voice"),
                ],
                default="text",
                max_length=20,
            ),
        ),
        # Allow blank content (attachments may have no text caption)
        migrations.AlterField(
            model_name="chatmessage",
            name="content",
            field=models.TextField(blank=True, default=""),
        ),
        # pinned_message FK on ChatRoom
        migrations.AddField(
            model_name="chatroom",
            name="pinned_message",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pinned_in_rooms",
                to="core.chatmessage",
            ),
        ),
    ]
