from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_chat_attachments_pinned_message"),
    ]

    operations = [
        # display_name on Ir for chat context
        migrations.AddField(
            model_name="ir",
            name="display_name",
            field=models.CharField(blank=True, max_length=45, null=True),
        ),
        # image_url on ChatRoom for group avatars
        migrations.AddField(
            model_name="chatroom",
            name="image_url",
            field=models.URLField(blank=True, max_length=1000, null=True),
        ),
        # ChatMessageReaction model
        migrations.CreateModel(
            name="ChatMessageReaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("emoji", models.CharField(max_length=8)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "ir",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_reactions",
                        to="core.ir",
                    ),
                ),
                (
                    "message",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reactions",
                        to="core.chatmessage",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["message", "emoji"], name="core_chatme_message_emoji_idx"),
                ],
                "unique_together": {("message", "ir", "emoji")},
            },
        ),
    ]
