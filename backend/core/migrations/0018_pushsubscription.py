from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0017_alter_uvdetail_uv_count'),
    ]

    operations = [
        migrations.CreateModel(
            name='PushSubscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('endpoint', models.TextField()),
                ('p256dh', models.CharField(max_length=255)),
                ('auth', models.CharField(max_length=255)),
                ('user_agent', models.CharField(blank=True, max_length=255, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('ir', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='push_subscriptions', to='core.ir')),
            ],
            options={
                'unique_together': {('ir', 'endpoint')},
            },
        ),
        migrations.AddIndex(
            model_name='pushsubscription',
            index=models.Index(fields=['ir'], name='core_pushsu_ir_cddbf9_idx'),
        ),
    ]
