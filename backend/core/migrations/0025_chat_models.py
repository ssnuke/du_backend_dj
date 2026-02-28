from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_alter_notification_notification_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatRoom",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("room_type", models.CharField(choices=[("direct", "Direct"), ("group", "Group")], default="group", max_length=20)),
                ("room_name", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="chat_rooms_created", to="core.ir")),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="ChatMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("message_type", models.CharField(choices=[("text", "Text")], default="text", max_length=20)),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("room", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="core.chatroom")),
                ("sender", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chat_messages", to="core.ir")),
            ],
            options={"ordering": ["-id"]},
        ),
        migrations.CreateModel(
            name="ChatRoomMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("joined_at", models.DateTimeField(auto_now_add=True)),
                ("added_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="chat_members_added", to="core.ir")),
                ("ir", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chat_memberships", to="core.ir")),
                ("room", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="core.chatroom")),
            ],
            options={"unique_together": {("room", "ir")}},
        ),
        migrations.CreateModel(
            name="ChatMessageReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("read_at", models.DateTimeField(auto_now_add=True)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="receipts", to="core.chatmessage")),
                ("reader", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="chat_receipts", to="core.ir")),
            ],
            options={"unique_together": {("message", "reader")}},
        ),
        migrations.AddIndex(
            model_name="chatmessage",
            index=models.Index(fields=["room", "id"], name="core_chatme_room_id_f57423_idx"),
        ),
        migrations.AddIndex(
            model_name="chatmessage",
            index=models.Index(fields=["room", "created_at"], name="core_chatme_room_id_43aab9_idx"),
        ),
        migrations.AddIndex(
            model_name="chatroommember",
            index=models.Index(fields=["room", "ir"], name="core_chatro_room_id_50d295_idx"),
        ),
        migrations.AddIndex(
            model_name="chatroommember",
            index=models.Index(fields=["ir", "joined_at"], name="core_chatro_ir_id_80d964_idx"),
        ),
        migrations.AddIndex(
            model_name="chatmessagereceipt",
            index=models.Index(fields=["message", "reader"], name="core_chatme_message_8057ec_idx"),
        ),
        migrations.AddIndex(
            model_name="chatmessagereceipt",
            index=models.Index(fields=["reader", "read_at"], name="core_chatme_reader__4a6fc2_idx"),
        ),
    ]
