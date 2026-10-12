import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0064_promotiondocument'),
    ]

    operations = [
        migrations.AddField(
            model_name='promotiondocument',
            name='uploaded_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='promotion_documents_uploaded', to='core.ir',
            ),
        ),
    ]
