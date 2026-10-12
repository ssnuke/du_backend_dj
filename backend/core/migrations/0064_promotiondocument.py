import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0063_learnvideoprogress_resume'),
    ]

    operations = [
        migrations.CreateModel(
            name='PromotionDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(
                    max_length=30,
                    choices=[
                        ('financial_explanation', 'Financial explanation'),
                        ('legal_notary', 'Legal notary'),
                        ('welcome_mandate_video', 'Welcome mandate video'),
                    ],
                )),
                ('file_key', models.CharField(max_length=300)),
                ('original_name', models.CharField(max_length=200)),
                ('content_type', models.CharField(max_length=100)),
                ('size_bytes', models.BigIntegerField()),
                ('uploaded_at', models.DateTimeField(auto_now=True)),
                ('ir', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='promotion_documents', to='core.ir')),
            ],
            options={'unique_together': {('ir', 'kind')}},
        ),
    ]
