# Generated migration for Pocket and PocketMember models

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_pocketmember_infodetail_info_type_and_more'),
    ]

    operations = [
        # Add Pocket model
        migrations.CreateModel(
            name='Pocket',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_pockets', to='core.ir')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pockets', to='core.team')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        # Add PocketMember model
        migrations.CreateModel(
            name='PocketMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('ADMIN', 'ADMIN'), ('CTC', 'CTC'), ('LDC', 'LDC'), ('LS', 'LS'), ('GC', 'GC'), ('IR', 'IR')], max_length=5)),
                ('joined_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('added_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pocket_members_added', to='core.ir')),
                ('ir', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pocket_memberships', to='core.ir')),
                ('pocket', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='core.pocket')),
                ('team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='pocket_members', to='core.team')),
            ],
        ),
        # Add unique constraint to Pocket (team, name)
        migrations.AddConstraint(
            model_name='pocket',
            constraint=models.UniqueConstraint(fields=['team', 'name'], name='unique_pocket_per_team'),
        ),
        # Add unique constraint to PocketMember (pocket, ir)
        migrations.AddConstraint(
            model_name='pocketmember',
            constraint=models.UniqueConstraint(fields=['pocket', 'ir'], name='unique_pocket_member'),
        ),
        # Add indexes to PocketMember
        migrations.AddIndex(
            model_name='pocketmember',
            index=models.Index(fields=['pocket', 'role'], name='pocket_role_idx'),
        ),
        migrations.AddIndex(
            model_name='pocketmember',
            index=models.Index(fields=['ir', 'team'], name='ir_team_idx'),
        ),
        migrations.AddIndex(
            model_name='pocketmember',
            index=models.Index(fields=['pocket', 'ir'], name='pocket_ir_idx'),
        ),
        # Update WeeklyTarget model to support pocket targets
        migrations.AddField(
            model_name='weeklytarget',
            name='pocket',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='weekly_targets', to='core.pocket'),
        ),
        migrations.AddField(
            model_name='weeklytarget',
            name='pocket_weekly_info_target',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='weeklytarget',
            name='pocket_weekly_plan_target',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='weeklytarget',
            name='pocket_weekly_uv_target',
            field=models.IntegerField(blank=True, null=True),
        ),
        # Add new unique constraint with pocket
        migrations.AlterUniqueTogether(
            name='weeklytarget',
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name='weeklytarget',
            constraint=models.UniqueConstraint(fields=['week_number', 'year', 'ir'], name='unique_ir_target'),
        ),
        migrations.AddConstraint(
            model_name='weeklytarget',
            constraint=models.UniqueConstraint(fields=['week_number', 'year', 'team'], name='unique_team_target'),
        ),
        migrations.AddConstraint(
            model_name='weeklytarget',
            constraint=models.UniqueConstraint(fields=['week_number', 'year', 'pocket'], name='unique_pocket_target'),
        ),
        # Add index for pocket targets
        migrations.AddIndex(
            model_name='weeklytarget',
            index=models.Index(fields=['pocket', 'week_number', 'year'], name='pocket_week_idx'),
        ),
    ]
