from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0053_event_workflow'),
    ]

    operations = [
        migrations.AddField(
            model_name='role',
            name='can_access_fleet',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='role',
            name='can_access_safety',
            field=models.BooleanField(default=False),
        ),
        # Populate: set fleet/safety = can_access_manage for existing roles
        migrations.RunSQL(
            "UPDATE core_role SET can_access_fleet = can_access_manage, can_access_safety = can_access_manage",
            reverse_sql="UPDATE core_role SET can_access_fleet = 0, can_access_safety = 0",
        ),
    ]
